"""Mock Codex facade (``mock-codex``).

Shares the exec engine with every other facade and differs only in Codex's wire
shape (``design/harness-adapters.md``): ``codex exec --json`` emits a **JSONL
event stream**, sessions are self-assigned (the id rides ``thread.started``), and
automated follow-ups are ``codex exec resume <id>`` / ``--last``.
"""

from __future__ import annotations

import argparse
import json
import sys

from blizzard_mock.harness.engine import RunResult
from blizzard_mock.harness.facades import _common
from blizzard_mock.harness.facades._text import render_ask_text

_USAGE = """\
mock-codex — mock Codex coding-harness facade

Usage:
  mock-codex exec [--json] "<script>"
  mock-codex exec resume <session-id> "<resume-script>"
  mock-codex exec --last "<resume-script>"

The prompt is the program (Python, exec()'d in the acquired worktree). Fenced —
refuses to run unless test scaffolding marks the environment.
"""


class CodexJsonlWire:
    """Render a :class:`RunResult` as Codex's ``exec --json`` JSONL event stream."""

    def __init__(self, json_mode: bool = True) -> None:
        self._json = json_mode

    def render(self, result: RunResult) -> str:
        text = render_ask_text(result) if result.subtype == "ask" else result.text
        if not self._json:
            return f"{text}\n"
        events = [
            {"type": "thread.started", "thread_id": result.session_id},
            {"type": "item.completed", "item": {"type": "agent_message", "text": text}},
            {"type": "turn.completed", "is_error": result.is_error, "num_turns": result.num_turns},
        ]
        return "".join(json.dumps(event) + "\n" for event in events)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="mock-codex", add_help=True)
    parser.add_argument("subcommand", nargs="?", default=None, help="'exec'")
    parser.add_argument("resume", nargs="?", default=None, help="'resume' or the script")
    parser.add_argument("rest", nargs="*", default=None, help="session id and/or script")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--last", action="store_true")
    parser.add_argument("--output-schema", default=None, help="accepted; the mock always emits tagged output")
    return parser


def _resolve(args: argparse.Namespace) -> tuple[str | None, str | None, bool]:
    """Return (script, session_id, is_resume) from Codex's positional grammar."""
    positionals = [p for p in (args.resume, *(args.rest or [])) if p is not None]
    if positionals and positionals[0] == "resume":
        # exec resume <session-id> "<script>"
        session_id = positionals[1] if len(positionals) > 1 else None
        script = positionals[2] if len(positionals) > 2 else _common.read_script(None)
        return script, session_id, True
    if args.last:
        # exec --last "<script>": resume the most recent session, so no id to bind.
        script = positionals[0] if positionals else _common.read_script(None)
        return script, None, True
    script = positionals[0] if positionals else _common.read_script(None)
    return script, None, False


def main(argv: list[str] | None = None) -> None:
    """Entry point for the ``mock-codex`` binary."""
    args = _parser().parse_args(sys.argv[1:] if argv is None else argv)
    if args.subcommand not in ("exec", None):
        print(_USAGE, file=sys.stderr)
        raise SystemExit(2)

    script, session_id, is_resume = _resolve(args)
    if script is None:
        print(_USAGE)
        raise SystemExit(0)
    if is_resume and session_id is None and not args.last:
        print("mock-codex: resume requires a session id", file=sys.stderr)
        raise SystemExit(2)

    wire = CodexJsonlWire(json_mode=args.json)
    code = _common.dispatch(
        wire=wire, script=script, session_id=session_id, is_resume=is_resume and session_id is not None
    )
    raise SystemExit(code)


if __name__ == "__main__":
    main()
