"""The terse helper library behavior scripts import.

Bound into the script namespace by :func:`~engine.run_prompt`; a script calls
``commit("msg")`` with no import, and everything downstream is real —
:func:`commit` makes a git commit, :func:`apply_diff` mutates real files.
"""

from __future__ import annotations

import json
import subprocess
import time
import uuid
from collections.abc import Sequence
from typing import Protocol, cast

from blizzard_mock.harness.engine import (
    CHOICE_CLOSE,
    CHOICE_OPEN,
    HarnessCrash,
    RunContext,
    RunResult,
    _AskExit,
    current_context,
)
from blizzard_mock.harness.internal import git
from blizzard_mock.harness.session import Ask, SessionState


def ask(question: str, options: Sequence[str] | None = None) -> None:
    """Fire the ask-and-exit protocol and end the turn (never returns normally).

    Records the ask on the session, stages an ``ask`` wire result, and
    unwinds the ``exec`` so the process exits rather than blocking or polling.
    """
    ctx = current_context()
    opts = list(options or [])
    record = Ask(question=question, options=opts)
    ctx.session.asks.append(record)
    if ctx.ask_cmd:
        args = [*ctx.ask_cmd, question]
        if opts:
            args += ["--options", "|".join(opts)]
        subprocess.run(args, cwd=ctx.cwd, env=dict(ctx.env), check=False)
    ctx.result = RunResult(session_id=ctx.session.session_id, subtype="ask", ask=record, text=question)
    raise _AskExit(record)


def apply_diff(diff: str) -> None:
    """Apply a unified ``diff`` to the acquired worktree (real ``git apply``)."""
    ctx = current_context()
    git.apply_diff(diff, cwd=ctx.cwd, env=ctx.env)
    _record_tool_turn(ctx, "Edit", {"diff": diff}, output="diff applied")


def commit(message: str) -> str:
    """Make a real ``git commit`` in the acquired worktree; return the commit sha."""
    ctx = current_context()
    sha = git.commit(message, cwd=ctx.cwd, env=ctx.env)
    _record_tool_turn(ctx, "Bash", {"command": f"git commit -am {message!r}"}, output=f"[main {sha[:7]}] {message}")
    return sha


def tool_call(name: str, tool_input: dict[str, object] | None = None, output: str = "ok") -> None:
    """Record one tool call that does nothing else — no git, no files touched.

    Fires the transcript pair and ``PostToolUse`` hook, ``name`` whatever the
    script says it is — a call timeline with no side effects.
    """
    ctx = current_context()
    _record_tool_turn(ctx, name, dict(tool_input or {}), output=output)


def _record_tool_turn(ctx: RunContext, name: str, tool_input: dict[str, object], *, output: str) -> None:
    """The effects of one tool call: the transcript pair, and the ``PostToolUse`` hooks.

    Two independent seams: the transcript pair is minted when a writer is
    wired; the hooks fire regardless.
    """
    if ctx.transcript is not None:
        tool_use_id = ctx.transcript.record_tool_call(name, tool_input)
        ctx.transcript.record_tool_result(tool_use_id, output)
    if ctx.hooks is not None:
        ctx.hooks.on_tool_use(name, tool_input, output)


def verdict(choice: str, assessment: str = "") -> None:
    """Emit the structured completion verdict in the facade's output format.

    Renders as ``<Choice>{choice}</Choice>``, with any ``assessment`` payload
    following; staged on the context, wrapped by the facade at turn end.
    """
    ctx = current_context()
    text = f"{CHOICE_OPEN}{choice}{CHOICE_CLOSE}"
    if assessment:
        text = f"{text}\n{assessment}"
    ctx.session.verdicts.append(choice)
    ctx.result = RunResult(session_id=ctx.session.session_id, subtype="success", text=text)


def hang() -> None:
    """Block forever, so a caller's stall/heartbeat/reap handling can be exercised.

    Never returns and never emits. Tests bound this with a subprocess timeout and
    assert the timeout fired.
    """
    while True:
        time.sleep(3600)


def crash(*, hard: bool = False) -> None:
    """Terminate abnormally — the worker dies without a verdict.

    Default raises :class:`HarnessCrash` (exit 1). ``hard=True`` bypasses all
    cleanup with ``os._exit`` for a truly un-graceful death.
    """
    if hard:
        import os

        os._exit(137)
    raise HarnessCrash("behavior script called crash()")


def usage_limited(*, resets_at: str = "5:40pm (America/Chicago)") -> None:
    """Simulate a Claude Code subscription usage-limit exit (blizzard#594) — the real
    2026-09-05 shape: a synthetic assistant transcript record (``isApiErrorMessage:
    true``, ``error: "rate_limit"``) in place of a turn's own reply. The runner's
    classifier reads this record alone, never the ordinary envelope this call still
    ends the turn with. Claude-Code-only; :func:`usage_limit_error` is OpenCode's own."""
    from blizzard_mock.harness.facades._transcript import ClaudeTranscriptWriter

    ctx = current_context()
    if not isinstance(ctx.transcript, ClaudeTranscriptWriter):
        raise RuntimeError("usage_limited() is Claude-Code-only — no Claude transcript writer is wired")
    text = f"You've hit your session limit · resets {resets_at}"
    ctx.transcript.record_raw(
        "assistant",
        {
            "model": "<synthetic>",
            "usage": {
                "input_tokens": 0,
                "output_tokens": 0,
                "cache_read_input_tokens": 0,
                "cache_creation_input_tokens": 0,
            },
            "content": [{"type": "text", "text": text}],
        },
        extra={"isApiErrorMessage": True, "error": "rate_limit"},
    )
    ctx.result = RunResult(session_id=ctx.session.session_id, subtype="success", text="")


def usage_limit_error(*, message: str | None = None) -> None:
    """Stage OpenCode's own captured usage-limit ``error`` event (blizzard#594 D6) — the
    ``AI_APICallError``/429 shape confirmed against the installed ``opencode-ai`` binary
    (see ``tests/opencode_usage_limit_fixture.py`` in the sibling ``blizzard`` checkout for
    its full provenance). OpenCode-only; :func:`usage_limited` is Claude Code's own."""
    ctx = current_context()
    wire = _opencode_wire(ctx, "usage_limit_error")
    event = {
        "type": "error",
        "sessionID": ctx.session.session_id,
        "error": {
            "name": "AI_APICallError",
            "data": {
                "message": message
                or (
                    "Usage limit reached. It will reset in 2 hours. "
                    "To continue using this model now, enable usage from your available balance"
                ),
                "statusCode": 429,
            },
        },
    }
    wire.wire_events.append(json.dumps(event))


def state() -> SessionState:
    """Return the current :class:`~blizzard_mock.harness.session.SessionState`.

    A resumed script reads this to see what it asked (``state().last_ask``) and
    the answer it was resumed with (``state().last_answer``).
    """
    return current_context().session


def answer() -> str | None:
    """Return the resume message this turn was resumed with (the answer), if any.

    On a ``<behavior-script>``-tagged resume this is the message's prose with
    its blocks elided; an untagged resume returns the whole raw message.
    """
    return current_context().session.last_answer


# -- The misbehaviour plane (D7) — OpenCode-only wire shapes ----------------- #
# Each stages a raw JSONL line on the active wire's own ``wire_events`` list.


class _OpenCodeCapableWire(Protocol):
    """The structural shape the misbehaviour-plane helpers need off the active wire —
    just enough to duck-type against, so this module never has to import a facade."""

    wire_events: list[str]


def _opencode_wire(ctx: RunContext, helper: str) -> _OpenCodeCapableWire:
    """Return ``ctx.wire`` narrowed to :class:`_OpenCodeCapableWire`, or refuse loudly
    when the active wire has no JSONL stream — checked via a duck-typed marker rather
    than an isinstance check, so this module never has to import a facade."""
    if not getattr(ctx.wire, "carries_opencode_wire_events", False):
        raise RuntimeError(
            f"{helper}() is OpenCode-only — {type(ctx.wire).__name__} has no JSONL event stream to carry it"
        )
    return cast(_OpenCodeCapableWire, ctx.wire)


def permission_denial(name: str, patterns: Sequence[str] | None = None, *, permission_id: str | None = None) -> None:
    """Stage an OpenCode ``permission`` event for a denied request.

    ``patterns`` defaults to a single wildcard. OpenCode-only.
    """
    ctx = current_context()
    wire = _opencode_wire(ctx, "permission_denial")
    event = {
        "type": "permission",
        "sessionID": ctx.session.session_id,
        "permission": {
            "id": permission_id or f"perm_{uuid.uuid4().hex[:16]}",
            "permission": name,
            "patterns": list(patterns) if patterns else ["*"],
        },
    }
    wire.wire_events.append(json.dumps(event))


def interrupt_tool(
    name: str,
    tool_input: dict[str, object] | None = None,
    *,
    error: str = "interrupted",
    call_id: str | None = None,
) -> None:
    """Stage a ``tool_use`` event whose part's state is ``"error"`` — how an
    interrupted or denied tool call reads on OpenCode's wire; it has no separate
    "interrupted" discriminator. OpenCode-only, like :func:`permission_denial`.
    """
    ctx = current_context()
    wire = _opencode_wire(ctx, "interrupt_tool")
    session_id = ctx.session.session_id
    event = {
        "type": "tool_use",
        "sessionID": session_id,
        "part": {
            "id": f"prt_{uuid.uuid4().hex[:16]}",
            "sessionID": session_id,
            "messageID": f"msg_{uuid.uuid4().hex[:16]}",
            "type": "tool",
            "callID": call_id or f"call_{uuid.uuid4().hex[:16]}",
            "tool": name,
            "state": {
                "status": "error",
                "input": dict(tool_input or {}),
                "output": None,
                "error": error,
                "title": None,
            },
        },
    }
    wire.wire_events.append(json.dumps(event))


def malformed_record(line: str | None = None) -> None:
    """Stage a raw line in the OpenCode JSONL stream the real parser rejects.

    Defaults to invalid JSON; pass ``line`` for a different rejection shape.
    """
    ctx = current_context()
    wire = _opencode_wire(ctx, "malformed_record")
    wire.wire_events.append(line if line is not None else '{"type": "tool_use", "sessionID":')
