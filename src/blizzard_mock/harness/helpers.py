"""The terse helper library behavior scripts import.

Bound into the script namespace by :func:`~engine.run_prompt`; a script calls
``commit("msg")`` with no import, and everything downstream is real —
:func:`commit` makes a git commit, :func:`apply_diff` mutates real files.
"""

from __future__ import annotations

import subprocess
import time
from collections.abc import Sequence

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
