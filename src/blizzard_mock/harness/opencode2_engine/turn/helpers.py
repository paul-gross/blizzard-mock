"""The helpers bound into every turn script's namespace, no import needed.

A script runs in its session's directory and environment, so raw Python acts on the real files beneath it; these
helpers are how it acts on the *conversation*. Each one is a request to the service: a text or reasoning part, a
step's usage, a tool call the session's permission rules judge before it runs, a child session running a script of
its own, a form, or a provider failure that ends the turn."""

from __future__ import annotations

import subprocess
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import cast

from ..domain import failures
from .channel import Channel

type Json = dict[str, object]


class TurnEnded(BaseException):
    """Raised by a helper that has already ended the turn with the service; the turn runner exits on it."""


@dataclass(frozen=True)
class ToolResult:
    """What one tool call came to. ``status`` is ``completed``, ``failed``, ``refused`` (a rule or a rejected
    permission request denied it), or ``withheld`` (a rule denying every resource kept the tool from the model, so
    no call was made)."""

    status: str
    output: str = ""
    exit_code: int | None = None
    session: str | None = None

    @property
    def ok(self) -> bool:
        return self.status == "completed"


@dataclass
class _Turn:
    channel: Channel
    header: Json


_CURRENT: list[_Turn] = []


def bind(channel: Channel, header: Json) -> None:
    """Make ``header``'s turn the one every helper acts on."""
    _CURRENT[:] = [_Turn(channel, header)]


def _turn() -> _Turn:
    if not _CURRENT:
        raise RuntimeError("no turn is bound: helpers run only inside a turn's child process")
    return _CURRENT[0]


# -- the turn ----------------------------------------------------------------------------------------------------------


def session_id() -> str:
    return str(_turn().header["session"])


def model() -> Json:
    found = _turn().header.get("model")
    return dict(cast(Json, found)) if isinstance(found, dict) else {}


def resumed() -> bool:
    """Whether this run is a restarted service resuming the turn its last lifetime was running."""
    return _turn().header.get("resumed") is True


def wake_result() -> str | None:
    """On a turn a background child's finish woke, that child's final text; otherwise ``None``."""
    woke = _turn().header.get("wake")
    return str(cast(Json, woke).get("text", "")) if isinstance(woke, dict) else None


# -- message parts -----------------------------------------------------------------------------------------------------


def text(content: str) -> None:
    """Add a text part to the current step; the turn's reply is its last step's text."""
    _turn().channel.send("text", text=content)


def reasoning(content: str) -> None:
    """Add a reasoning part to the current step."""
    _turn().channel.send("reasoning", text=content)


def usage(
    *, input: int = 0, output: int = 0, reasoning: int = 0, cache_read: int = 0, cache_write: int = 0, cost: float = 0
) -> None:
    """Set the current step's tokens and cost, in place of the figures the service would otherwise generate."""
    tokens: Json = {
        "input": input,
        "output": output,
        "reasoning": reasoning,
        "cache": {"read": cache_read, "write": cache_write},
    }
    _turn().channel.send("usage", tokens=tokens, cost=cost)


def step() -> None:
    """End the current step, as a model call ending in tool calls does, and start the next."""
    _turn().channel.send("step")


# -- tools -------------------------------------------------------------------------------------------------------------


def _call(name: str, tool_input: Json, resource: str, *, ask: bool = False) -> tuple[str, str]:
    reply = _turn().channel.ask("tool", name=name, input=tool_input, resource=resource, ask=ask)
    return str(reply.get("verdict")), str(reply.get("call", ""))


def _refused(verdict: str) -> ToolResult | None:
    if verdict == "allow":
        return None
    if verdict == "interrupted":
        raise TurnEnded
    return ToolResult("withheld" if verdict == "withheld" else "refused")


def shell(command: str, *, timeout: float | None = None) -> ToolResult:
    """Run ``command`` under ``/bin/sh`` in the session's directory and environment, once the rules allow it."""
    verdict, call = _call("shell", {"command": command}, command)
    if (refused := _refused(verdict)) is not None:
        return refused
    try:
        ran = subprocess.run(
            ["/bin/sh", "-c", command], stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=timeout
        )
    except subprocess.TimeoutExpired:
        _turn().channel.send("tool.failed", call=call, kind="tool.failed", message=f"timed out: {command}")
        return ToolResult("failed")
    output = ran.stdout + ran.stderr
    metadata: Json = {"exit": ran.returncode, "status": "completed", "truncated": False}
    _turn().channel.send("tool.done", call=call, output=output, metadata=metadata)
    return ToolResult("completed", output, ran.returncode)


def read(path: str) -> ToolResult:
    """Read ``path`` relative to the session's directory, once the rules allow it."""
    verdict, call = _call("read", {"path": path}, path)
    if (refused := _refused(verdict)) is not None:
        return refused
    try:
        with open(path, encoding="utf-8") as handle:
            content = handle.read()
    except OSError as exc:
        _turn().channel.send("tool.failed", call=call, kind="tool.failed", message=str(exc))
        return ToolResult("failed")
    _turn().channel.send("tool.done", call=call, output=content, metadata={"truncated": False})
    return ToolResult("completed", content)


def tool(
    name: str, tool_input: Mapping[str, object] | None = None, *, resource: str = "*", output: str = "ok"
) -> ToolResult:
    """Record a call of tool ``name`` on ``resource`` that does nothing else, once the rules allow it — an MCP tool,
    say, or any tool the mock does not otherwise act out."""
    verdict, call = _call(name, dict(tool_input or {}), resource)
    if (refused := _refused(verdict)) is not None:
        return refused
    _turn().channel.send("tool.done", call=call, output=output, metadata={"truncated": False})
    return ToolResult("completed", output)


def permission_request(action: str, resource: str) -> ToolResult:
    """Call tool ``action`` on ``resource`` through a permission request, whatever the rules would allow: the turn
    holds until the request is answered, and a rule denying the call still refuses it outright."""
    verdict, call = _call(action, {"resource": resource}, resource, ask=True)
    if (refused := _refused(verdict)) is not None:
        return refused
    _turn().channel.send("tool.done", call=call, output="ok", metadata={"truncated": False})
    return ToolResult("completed", "ok")


def subagent(script: str, *, background: bool = False, description: str = "", wake: str | None = None) -> ToolResult:
    """Start a child session whose turn runs ``script`` in a child process of its own. A foreground call waits for
    the child and returns its final text; a ``background`` one returns at once, and when the child finishes it wakes
    this session through its inbox to run ``wake`` (a script, ``wake_result()`` holding the child's text) — after
    this turn has gone idle, if the child outlives it."""
    reply = _turn().channel.ask("subagent", script=script, background=background, description=description, wake=wake)
    verdict = str(reply.get("verdict"))
    if (refused := _refused(verdict)) is not None:
        return refused
    status = "completed" if reply.get("status") == "completed" else "failed"
    child = reply.get("session")
    return ToolResult(status, str(reply.get("output", "")), session=str(child) if child else None)


def form(title: str, fields: Sequence[Mapping[str, object]] | None = None) -> Json:
    """Raise a form and hold the turn until it is cancelled; the form as the service listed it."""
    reply = _turn().channel.ask("form", title=title, fields=[dict(field) for field in fields or ()])
    raised = reply.get("form")
    if not isinstance(raised, dict):
        raise TurnEnded
    return dict(cast(Json, raised))


# -- the turn's end ----------------------------------------------------------------------------------------------------


def _fail(error: Json) -> None:
    _turn().channel.send("fail", error=error)
    raise TurnEnded


def provider_refusal(message: str | None = None) -> None:
    """End the turn as the provider refusing it."""
    _fail(failures.provider_refusal(*([message] if message is not None else [])))


def usage_limit(retry_after_ms: int | None = 18_748_000, *, kind: str = "provider.free-limit") -> None:
    """End the turn on a usage limit, ``retry_after_ms`` the provider's reset (``None`` for a limit with none)."""
    _fail(failures.usage_limit(retry_after_ms, kind=kind))


def overload(message: str | None = None) -> None:
    """End the turn as an overloaded provider."""
    _fail(failures.overload(*([message] if message is not None else [])))


def unknown_model() -> None:
    """End the turn as the session's model not routing."""
    _fail(failures.unknown_model(model()))


def unknown_variant() -> None:
    """End the turn as the session's variant not routing."""
    _fail(failures.unknown_variant(model()))


def hang() -> None:
    """Hold the turn until it is interrupted."""
    while True:
        time.sleep(3600)


def namespace() -> dict[str, object]:
    """The globals a turn script runs with."""
    names = (
        ToolResult,
        form,
        hang,
        model,
        overload,
        permission_request,
        provider_refusal,
        read,
        reasoning,
        resumed,
        session_id,
        shell,
        step,
        subagent,
        text,
        tool,
        unknown_model,
        unknown_variant,
        usage,
        usage_limit,
        wake_result,
    )
    return {"__name__": "__behavior_script__", **{item.__name__: item for item in names}}


__all__ = [
    "ToolResult",
    "TurnEnded",
    "bind",
    "form",
    "hang",
    "model",
    "namespace",
    "overload",
    "permission_request",
    "provider_refusal",
    "read",
    "reasoning",
    "resumed",
    "session_id",
    "shell",
    "step",
    "subagent",
    "text",
    "tool",
    "unknown_model",
    "unknown_variant",
    "usage",
    "usage_limit",
    "wake_result",
]
