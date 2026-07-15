"""Mock Claude Code facade (``mock-claude-code``).

Presents Claude Code's non-interactive CLI/wire surface to the runner's adapter
and turns the received prompt into behavior by ``exec``'ing it as Python in the
acquired worktree — *the prompt is the program*. Only the CLI shape and the
output format live here; the behavior, fence, and session state are the shared
:mod:`blizzard_mock.harness.engine`.

Surface mimicked (``design/harness-adapters.md``):

- Headless run: ``mock-claude-code -p [--output-format json] "<script>"`` (prompt
  via arg or stdin).
- Pre-assigned session: ``--session-id <uuid>`` (Claude Code honors the hint).
- Automated follow-up: ``mock-claude-code -p --resume <sid> "<message>"`` — the
  resume message arrives as code, executed with the prior session state visible.
- ``--output-format json`` emits the single ``{"type":"result", …}`` envelope the
  adapter's ``verdict`` parses (``<Choice>{name}</Choice>`` rides ``result``).
- ``--settings <path>`` (the runner-owned worker hook file) is accepted and
  ignored — the mock has no hooks to load.
- ``--model <name>`` (the pinned worker model) is accepted and ignored — the mock
  is model-agnostic.

Fenced: the engine refuses to run unless test scaffolding marks the environment,
so ``mock-claude-code`` can never pass as a real ``claude`` binding.
"""

from __future__ import annotations

import argparse
import json
import sys

from blizzard_mock.harness.engine import RunResult
from blizzard_mock.harness.facades import _common
from blizzard_mock.harness.facades._text import render_ask_text

_USAGE = """\
mock-claude-code — mock Claude Code coding-harness facade

Usage:
  mock-claude-code -p [--output-format text|json] [--session-id <uuid>] "<script>"
  mock-claude-code -p --resume <session-id> "<resume-script>"

The prompt is the program: it is Python, exec()'d in the acquired worktree with
the helper surface (ask/apply_diff/commit/verdict/hang/crash) bound. Fenced —
refuses to run unless test scaffolding marks the environment.

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
        # turn successfully, the ask riding the result text for the adapter.
        subtype = "success" if result.subtype == "ask" else result.subtype
        envelope = {
            "type": "result",
            "subtype": subtype,
            "is_error": result.is_error,
            "result": text,
            "session_id": result.session_id,
            "num_turns": result.num_turns,
            "duration_ms": result.duration_ms,
        }
        return json.dumps(envelope) + "\n"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="mock-claude-code", add_help=True)
    parser.add_argument("prompt", nargs="?", default=None)
    parser.add_argument("-p", "--print", action="store_true", dest="print_mode")
    parser.add_argument("--output-format", choices=["text", "json"], default="text")
    parser.add_argument("--session-id", default=None)
    parser.add_argument("--resume", default=None, metavar="SESSION_ID")
    parser.add_argument("--settings", default=None, help="accepted and ignored (mock has no hooks)")
    parser.add_argument("--model", default=None, help="accepted and ignored (mock is model-agnostic)")
    return parser


def main(argv: list[str] | None = None) -> None:
    """Entry point for the ``mock-claude-code`` binary."""
    args = _parser().parse_args(sys.argv[1:] if argv is None else argv)
    script = _common.read_script(args.prompt)
    if script is None:
        print(_USAGE)
        raise SystemExit(0)

    wire = ClaudeCodeWire(args.output_format)
    if args.resume is not None:
        code = _common.dispatch(wire=wire, script=script, session_id=args.resume, is_resume=True)
    else:
        code = _common.dispatch(wire=wire, script=script, session_id=args.session_id, is_resume=False)
    raise SystemExit(code)


if __name__ == "__main__":
    main()
