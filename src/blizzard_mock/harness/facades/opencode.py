"""Mock OpenCode facade (``mock-opencode``).

Two wholly separate modes live behind this one binary:

- ``run`` — differs from other facades only in OpenCode's wire shape: a
  server-assigned session, message printed as text, with a machine-readable
  trailer appended so the adapter can recover the session id. Shares the
  exec engine (``blizzard_mock.harness.engine``) with ``claude_code.py`` and
  ``codex.py``.
- ``emit`` — bakes a validated lever set into a standalone zipapp CLI-surface
  artifact (``blizzard_mock.harness.opencode_surface``); see
  ``harness/README.md``'s "OpenCode CLI-surface mode" section. Wholly
  separate from the exec engine: it shares no code with ``run`` beyond this
  module's argv dispatch.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from blizzard_mock.harness.engine import RunResult
from blizzard_mock.harness.facades import _common
from blizzard_mock.harness.facades._text import render_ask_text
from blizzard_mock.harness.opencode_surface import emit as surface_emit
from blizzard_mock.harness.opencode_surface import levers as surface_levers

#: Unchanged byte-for-byte from before ``emit`` existed — every ``run``-path
#: caller of this constant (bare invocation, a bad subcommand) must keep
#: printing exactly this, so ``emit`` gets its own separate usage text below
#: instead of extending this one.
_USAGE = """\
mock-opencode — mock OpenCode coding-harness facade

Usage:
  mock-opencode run [--session <id>] "<script>"
  mock-opencode run --attach --session <id> "<resume-script>"

The prompt is the program (Python, exec()'d in the acquired worktree). Fenced —
refuses to run unless test scaffolding marks the environment.
"""

#: ``emit --help``'s description — wholly separate from ``run``'s ``_USAGE`` above.
_EMIT_DESCRIPTION = (
    "Write a standalone CLI-surface fake-OpenCode zipapp. Wholly separate from "
    "run's exec engine: the emitted artifact answers OpenCode's real CLI/HTTP "
    "surface (--version, run, export, serve, attach, debug config --pure) as an "
    "unfenced, stdlib-only, out-of-process subprocess. See harness/README.md's "
    "'OpenCode CLI-surface mode' section, and "
    "blizzard_mock.harness.opencode_surface.levers.CATALOG for the lever roster."
)


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


def _emit_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="mock-opencode emit", description=_EMIT_DESCRIPTION, add_help=True)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--lever", dest="levers", action="append", default=[], metavar="NAME")
    parser.add_argument("--version", default="1.18.25")
    parser.add_argument("--auth-read-marker", default=None)
    parser.add_argument("--version-touch-path", default=None)
    return parser


def _run_emit(argv: list[str]) -> int:
    """``mock-opencode emit`` — validate the lever set, then write the zipapp.

    Writes no file at all when an unknown lever name is given (validated
    before anything touches the filesystem, never create-then-delete).
    """
    args = _emit_parser().parse_args(argv)
    try:
        levers = surface_levers.parse(args.levers)
    except ValueError as exc:
        print(f"mock-opencode emit: {exc}", file=sys.stderr)
        return 2
    surface_emit.emit(
        args.out,
        levers=levers,
        version=args.version,
        auth_read_marker=args.auth_read_marker,
        version_touch_path=args.version_touch_path,
    )
    return 0


def main(argv: list[str] | None = None) -> None:
    """Entry point for the ``mock-opencode`` binary.

    ``emit`` is intercepted here, before any of ``run``'s own argparse/dispatch
    runs — ``run``'s path below is untouched from before ``emit`` existed.
    """
    args_list = sys.argv[1:] if argv is None else list(argv)
    if args_list and args_list[0] == "emit":
        raise SystemExit(_run_emit(args_list[1:]))

    args = _parser().parse_args(args_list)
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
