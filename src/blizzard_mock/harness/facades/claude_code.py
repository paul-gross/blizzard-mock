"""Mock Claude Code facade (``mock-claude-code``).

Presents Claude Code's non-interactive CLI/wire surface and turns the received
prompt into behavior by ``exec``'ing it as Python in the acquired worktree — *the
prompt is the program*. Only the CLI shape and the output format live here; the
behavior, fence, and session state are the shared
:mod:`blizzard_mock.harness.engine`.

Surface mimicked (``design/harness-adapters.md``):

- Headless run: ``mock-claude-code -p [--output-format json] "<script>"`` (prompt
  via arg or stdin).
- Pre-assigned session: ``--session-id <uuid>`` (Claude Code honors the hint).
- Automated follow-up: ``mock-claude-code -p --resume <sid> "<message>"`` — the
  resume message arrives as code, executed with the prior session state visible.
- ``--output-format json`` emits the single ``{"type":"result", …}`` envelope
  (``<Choice>{name}</Choice>`` rides ``result``).
- ``--settings <path>`` (the runner-owned worker hook file) is read, and the
  ``PostToolUse`` / ``SessionEnd`` commands it declares are executed as real
  subprocesses — see the package README's "Hook execution".
- ``--model <name>`` (the pinned worker model) is accepted and ignored — the mock
  is model-agnostic.

Fenced: the engine refuses to run unless test scaffolding marks the environment,
so ``mock-claude-code`` can never pass as a real ``claude`` binding.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Mapping
from pathlib import Path

from blizzard_mock.harness.engine import RunResult, acquired_worktree, fence_base_dir
from blizzard_mock.harness.facades import _common
from blizzard_mock.harness.facades._hooks import build_hook_runner
from blizzard_mock.harness.facades._text import render_ask_text
from blizzard_mock.harness.facades._transcript import ClaudeTranscriptWriter, transcripts_root
from blizzard_mock.harness.facades._usage import MOCK_MODEL, synthesize_cost_usd, synthesize_usage_tokens

_USAGE = """\
mock-claude-code — mock Claude Code coding-harness facade

Usage:
  mock-claude-code -p [--output-format text|json] [--session-id <uuid>] [--settings <path>] "<script>"
  mock-claude-code -p --resume <session-id> [--settings <path>] "<resume-script>"

--settings names a Claude Code settings document; its PostToolUse and SessionEnd
hook commands are executed as real subprocesses.

The prompt is the program: it is Python, exec()'d in the acquired worktree with
the helper surface bound: ask, apply_diff, commit, tool_call, verdict, hang,
crash, state, answer. Fenced — refuses to run unless test scaffolding marks the
environment.

See src/blizzard_mock/harness/README.md for the full contract.
"""


class ClaudeCodeWire:
    """Render a :class:`RunResult` as Claude Code's headless output.

    ``--output-format json`` -> the single ``result`` envelope; ``text`` -> just
    the assistant's final message (the tagged ask/verdict text).
    """

    def __init__(self, output_format: str = "text") -> None:
        self._format = output_format

    def render(self, result: RunResult) -> str:
        text = render_ask_text(result) if result.subtype == "ask" else result.text
        if self._format != "json":
            return f"{text}\n"
        # Claude Code has no "ask" result subtype; a parked worker simply ends its
        # turn successfully, the ask riding the result text.
        subtype = "success" if result.subtype == "ask" else result.subtype
        # A realistic `usage` + `total_cost_usd` (blizzard epic #57): deterministic,
        # not tied to any real pricing (`_usage.py`).
        usage = synthesize_usage_tokens(text)
        envelope = {
            "type": "result",
            "subtype": subtype,
            "is_error": result.is_error,
            "result": text,
            "session_id": result.session_id,
            "num_turns": result.num_turns,
            "duration_ms": result.duration_ms,
            "model": MOCK_MODEL,
            "usage": usage,
            "total_cost_usd": synthesize_cost_usd(usage),
        }
        return json.dumps(envelope) + "\n"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="mock-claude-code", add_help=True)
    parser.add_argument("prompt", nargs="?", default=None)
    parser.add_argument("-p", "--print", action="store_true", dest="print_mode")
    parser.add_argument("--output-format", choices=["text", "json"], default="text")
    parser.add_argument("--session-id", default=None)
    parser.add_argument("--resume", default=None, metavar="SESSION_ID")
    parser.add_argument(
        "--settings",
        default=None,
        help="worker hook settings document; its PostToolUse/SessionEnd commands are executed",
    )
    # Both are RECORDED onto the session state (issue #144) and otherwise ignored.
    parser.add_argument("--model", default=None, help="recorded onto the session, not acted on")
    parser.add_argument("--effort", default=None, help="recorded onto the session, not acted on")
    return parser


def _build_transcript_writer(
    session_id: str | None, *, cwd: Path, env: Mapping[str, str]
) -> ClaudeTranscriptWriter | None:
    """The Claude-shaped transcript writer for this run, else ``None``.

    Only constructed when a session id is already known. A bare direct invocation
    that lets the engine self-assign a uuid (no ``--session-id``/``--resume``) skips
    transcript writing — that path has no session id to key the file on until the
    engine mints one.

    ``cwd``/``env`` are resolved once by :func:`main` and passed in, so the writer
    and the hook runner cannot disagree about which worktree this run acquired.
    """
    if not session_id:
        return None
    root = transcripts_root(env, fence_dir=fence_base_dir(cwd))
    return ClaudeTranscriptWriter(session_id=session_id, root=root, cwd=cwd)


def main(argv: list[str] | None = None) -> None:
    """Entry point for the ``mock-claude-code`` binary."""
    args = _parser().parse_args(sys.argv[1:] if argv is None else argv)
    script = _common.read_script(args.prompt)
    if script is None:
        print(_USAGE)
        raise SystemExit(0)

    wire = ClaudeCodeWire(args.output_format)
    is_resume = args.resume is not None
    session_id = args.resume if is_resume else args.session_id
    # Resolved once and shared: a second, independent resolution could disagree with
    # the first about which worktree this run acquired.
    env: Mapping[str, str] = os.environ
    cwd = acquired_worktree(env, Path.cwd())
    transcript = _build_transcript_writer(session_id, cwd=cwd, env=env)
    hooks = build_hook_runner(
        args.settings,
        cwd=cwd,
        env=env,
        session_id=session_id,
        transcript_path=transcript.path if transcript is not None else None,
    )
    code = _common.dispatch(
        wire=wire,
        script=script,
        session_id=session_id,
        is_resume=is_resume,
        transcript=transcript,
        hooks=hooks,
        # Recorded onto the session state, not acted on (issue #144).
        model=args.model,
        effort=args.effort,
    )
    raise SystemExit(code)


if __name__ == "__main__":
    main()
