"""The terse helper library behavior scripts import.

A mock-harness prompt *is* a Python script; these helpers keep the common cases
one line each — ``apply_diff`` / ``commit`` / ``verdict`` / ``ask`` / ``hang`` /
``crash`` — with raw Python underneath for the weird cases. They are bound into
the script namespace by :func:`blizzard_mock.harness.engine.run_prompt`, so a
script calls ``commit("msg")`` with no import; called outside an engine run they
raise (there is no ambient context).

Everything downstream of the harness seam runs for real: :func:`commit` makes a
real git commit in the acquired worktree, :func:`apply_diff` mutates real files.
:func:`ask` and :func:`verdict` record on the session and stage the wire result
the facade renders; :func:`ask` then exits the turn (ask-and-exit,
``design/ask-answer.md``).
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

    Records the ask on the session (so the resumed script can read
    ``state().last_ask``), optionally shells out to the real ``blizzard runner
    ask`` when the runner wired one via ``BLIZZARD_RUNNER_ASK_CMD``, stages an
    ``ask`` wire result, and unwinds the ``exec`` so the process exits — the
    worker does not block, spin, or poll (``design/ask-answer.md``).
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


def _record_tool_turn(ctx: RunContext, name: str, tool_input: dict[str, object], *, output: str) -> None:
    """Mint a matched ``tool_use``/``tool_result`` pair on the transcript, if one is wired.

    ``apply_diff`` and ``commit`` are the two real "tool calls" a mock script
    performs, so pairing them is the fidelity worth having: a mocked conversation
    shows real-looking tool turns with output, with no extra work from the
    behavior-script author. No-op when no transcript writer is bound (every facade
    but claude_code, and any direct engine caller that passes none).
    """
    if ctx.transcript is None:
        return
    tool_use_id = ctx.transcript.record_tool_call(name, tool_input)
    ctx.transcript.record_tool_result(tool_use_id, output)


def verdict(choice: str, assessment: str = "") -> None:
    """Emit the structured completion verdict in the facade's output format.

    Renders as the tagged ``<Choice>{choice}</Choice>`` shape the runner's
    adapter parses (``design/harness-adapters.md``), with any ``assessment``
    payload following. Staged on the context; the facade wraps it in its native
    envelope (Claude Code JSON, Codex JSONL, …) when the turn ends.
    """
    ctx = current_context()
    text = f"{CHOICE_OPEN}{choice}{CHOICE_CLOSE}"
    if assessment:
        text = f"{text}\n{assessment}"
    ctx.session.verdicts.append(choice)
    ctx.result = RunResult(session_id=ctx.session.session_id, subtype="success", text=text)


def hang() -> None:
    """Block forever, so the runner's stall/heartbeat/REAP handling can be exercised.

    Never returns and never emits — a hung worker makes no tool calls, its lease
    goes stale, and the supervisor reaps it. Tests bound this with a subprocess
    timeout and assert the timeout fired.
    """
    while True:
        time.sleep(3600)


def crash(*, hard: bool = False) -> None:
    """Terminate abnormally — the worker dies without a verdict.

    Default raises :class:`HarnessCrash`, which the engine renders as an error
    run (exit 1). ``hard=True`` bypasses all cleanup with ``os._exit`` for the
    rare test that needs a truly un-graceful death (no output, no state flush).
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
    """Return the resume message this turn was resumed with (the answer), if any."""
    return current_context().session.last_answer
