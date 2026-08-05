"""Mock OpenCode facade (``mock-opencode``).

Differs from other facades only in OpenCode's wire shape: a server-assigned
session, message printed as text, with a machine-readable trailer appended so
the adapter can recover the session id.
"""

from __future__ import annotations

import argparse
import json
import sys

from blizzard_mock.harness.engine import RunResult
from blizzard_mock.harness.facades import _common
from blizzard_mock.harness.facades._text import render_ask_text

_USAGE = """\
mock-opencode — mock OpenCode coding-harness facade

Usage:
  mock-opencode run [--session <id>] "<script>"
  mock-opencode run --attach --session <id> "<resume-script>"

The prompt is the program (Python, exec()'d in the acquired worktree). Fenced —
refuses to run unless test scaffolding marks the environment.
"""


class OpenCodeWire:
    """Render a :class:`RunResult` the OpenCode way: message text + a JSON trailer."""

    def render(self, result: RunResult) -> str:
        text = render_ask_text(result) if result.subtype == "ask" else result.text
        trailer = json.dumps({"session": result.session_id, "error": result.is_error, "turns": result.num_turns})
        return f"{text}\n{trailer}\n"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="mock-opencode", add_help=True)
    parser.add_argument("subcommand", nargs="?", default=None, help="'run'")
    parser.add_argument("prompt", nargs="?", default=None)
    parser.add_argument("--session", default=None)
    parser.add_argument("--attach", action="store_true", help="deliver a follow-up into an existing session")
    return parser


def main(argv: list[str] | None = None) -> None:
    """Entry point for the ``mock-opencode`` binary."""
    args = _parser().parse_args(sys.argv[1:] if argv is None else argv)
    if args.subcommand not in ("run", None):
        print(_USAGE, file=sys.stderr)
        raise SystemExit(2)

    script = _common.read_script(args.prompt)
    if script is None:
        print(_USAGE)
        raise SystemExit(0)
    if args.attach and not args.session:
        print("mock-opencode: --attach requires --session", file=sys.stderr)
        raise SystemExit(2)

    wire = OpenCodeWire()
    code = _common.dispatch(wire=wire, script=script, session_id=args.session, is_resume=args.attach)
    raise SystemExit(code)


if __name__ == "__main__":
    main()
