"""Mock OpenCode facade (``mock-opencode``).

Two modes: ``run`` shares the exec engine with every facade, differing only in wire shape —
a JSONL event stream, server-minted root ``sessionID``, a bare ``--session`` as a no-op
takeover; ``emit`` bakes a lever set into a standalone CLI-surface zipapp
(``opencode_surface`` — see ``harness/README.md``'s "OpenCode CLI-surface mode")."""

from __future__ import annotations

import argparse
import json
import os
import sys
import uuid
from collections.abc import Callable, Mapping
from pathlib import Path

from blizzard_mock.harness.engine import ITranscriptWriter, RunResult, acquired_worktree, fence_base_dir
from blizzard_mock.harness.facades import _common
from blizzard_mock.harness.facades._opencode_transcript import (
    _MOCK_VERSION,
    OpenCodeTranscriptWriter,
    document_path,
    transcripts_root,
)
from blizzard_mock.harness.facades._text import render_ask_text
from blizzard_mock.harness.facades._usage import synthesize_cost_usd, synthesize_usage_tokens
from blizzard_mock.harness.opencode_surface import emit as surface_emit
from blizzard_mock.harness.opencode_surface import levers as surface_levers

#: Unchanged byte-for-byte from before ``emit`` existed — ``emit`` gets its own usage text below.
_USAGE = """\
mock-opencode — mock OpenCode coding-harness facade

Usage:
  mock-opencode run [--model <name>] [--variant <v>] [--auto] "<script>"
  mock-opencode run --session <id> [--variant <v>] [--auto] "<resume-script>"
  mock-opencode --session <id> [--model <name>] [--variant <v>]   (interactive takeover)

The prompt is the program (Python, exec()'d in the acquired worktree). Fenced —
refuses to run unless test scaffolding marks the environment. A fresh ``run`` mints
its own session id, never the caller's — the caller learns it off the first emitted
event, exactly as the real fresh-session handshake requires.
"""

#: Appended to ``_USAGE`` only for an explicit ``-h``/``--help``, never for the pinned bare-invocation fallback above.
_EMIT_MENTION = (
    "\nAlso: mock-opencode emit --out <path> [--lever NAME]... — writes a standalone "
    "CLI-surface fake-OpenCode zipapp. See harness/README.md's 'OpenCode CLI-surface mode' "
    "section.\n"
)

#: ``emit --help``'s description — wholly separate from ``run``'s ``_USAGE`` above.
_EMIT_DESCRIPTION = (
    "Write a standalone CLI-surface fake-OpenCode zipapp. Wholly separate from "
    "run's exec engine: the emitted artifact answers OpenCode's real CLI/HTTP "
    "surface (--version, run, export, serve, attach, debug config --pure) as an "
    "unfenced, stdlib-only, out-of-process subprocess. See harness/README.md's "
    "'OpenCode CLI-surface mode' section, and "
    "blizzard_mock.harness.opencode_surface.levers.CATALOG for the lever roster."
)

#: The step-finish "reason" a completed turn reports; any nonempty string is faithful.
_STEP_REASON = "stop"

#: The provider/process error name a crashed behavior script is reported under.
_ERROR_NAME = "ProviderError"


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:16]}"


def _step_start_event(session_id: str, message_id: str) -> dict[str, object]:
    return {
        "type": "step_start",
        "sessionID": session_id,
        "part": {
            "id": _new_id("prt"),
            "sessionID": session_id,
            "messageID": message_id,
            "type": "step-start",
        },
    }


def _text_event(session_id: str, message_id: str, text: str) -> dict[str, object]:
    return {
        "type": "text",
        "sessionID": session_id,
        "part": {
            "id": _new_id("prt"),
            "sessionID": session_id,
            "messageID": message_id,
            "type": "text",
            "text": text,
        },
    }


def _step_finish_event(session_id: str, message_id: str, text: str) -> dict[str, object]:
    """A completed model step: deterministic usage/cost synthesized off ``text``'s
    length, the same illustrative approach ``_usage.py`` already gives Claude Code —
    a test injecting a desired shape does so the same way every other mock does:
    through the text the behavior script itself produced (``verdict``'s assessment,
    an ``ask`` question), not a second knob."""
    usage = synthesize_usage_tokens(text)
    return {
        "type": "step_finish",
        "sessionID": session_id,
        "part": {
            "id": _new_id("prt"),
            "sessionID": session_id,
            "messageID": message_id,
            "type": "step-finish",
            "reason": _STEP_REASON,
            "tokens": {
                "input": usage["input_tokens"],
                "output": usage["output_tokens"],
                "reasoning": 0,
                "cache": {
                    "read": usage["cache_read_input_tokens"],
                    "write": usage["cache_creation_input_tokens"],
                },
            },
            "cost": synthesize_cost_usd(usage),
        },
    }


def _error_event(session_id: str, message: str) -> dict[str, object]:
    """A session error is a distinct event, never a step-finish — matching the real
    adapter's ``_session_error``, which reads it off exactly this shape, and its
    ``has_usable_output``, which a crashed turn with no step-finish correctly fails."""
    return {
        "type": "error",
        "sessionID": session_id,
        "error": {
            "name": _ERROR_NAME,
            "data": {"message": message},
        },
    }


class OpenCodeRunWire:
    """Render a :class:`RunResult` as OpenCode's JSONL stream; every record carries
    the root ``sessionID`` from its first line. ``render_identity`` streams that
    first ``step_start`` record as soon as a fresh mint self-assigns it, matching
    the real handshake's early identity rather than waiting for process exit."""

    #: The marker ``helpers.py``'s misbehaviour-plane primitives (D7:
    #: ``permission_denial``, ``interrupt_tool``, ``malformed_record``) duck-type
    #: against — only this wire has a JSONL stream those raw lines can ride on.
    carries_opencode_wire_events = True

    def __init__(self) -> None:
        self._streamed_message_id: str | None = None

    def render_identity(self, session_id: str) -> str:
        """Mint and return the identity-bearing ``step_start`` line, remembering its
        message id so the later :meth:`render` call doesn't emit it a second time."""
        self._streamed_message_id = _new_id("msg")
        return json.dumps(_step_start_event(session_id, self._streamed_message_id)) + "\n"

    def render(self, result: RunResult) -> str:
        text = render_ask_text(result) if result.subtype == "ask" else result.text
        session_id = result.session_id
        message_id = self._streamed_message_id or _new_id("msg")
        lines: list[str] = []
        if self._streamed_message_id is None:
            lines.append(json.dumps(_step_start_event(session_id, message_id)))
        # The misbehaviour plane (D7): raw, pre-rendered lines a behavior script
        # staged via permission_denial/interrupt_tool/malformed_record, spliced in
        # call order — they represent things that happened mid-turn, so they go
        # before the turn's own closing text/error record.
        lines.extend(result.wire_events)
        if result.is_error:
            lines.append(json.dumps(_error_event(session_id, text or "the worker ended without a verdict")))
        else:
            lines.append(json.dumps(_text_event(session_id, message_id, text)))
            lines.append(json.dumps(_step_finish_event(session_id, message_id, text)))
        return "".join(line + "\n" for line in lines)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="mock-opencode", add_help=True)
    parser.add_argument("subcommand", nargs="?", default=None, help="'run', or absent for a takeover")
    parser.add_argument("prompt", nargs="?", default=None)
    parser.add_argument("--session", default=None)
    parser.add_argument("--model", default=None, help="mint-only; recorded onto the session, not acted on")
    parser.add_argument("--variant", default=None, help="reasoning effort; recorded onto the session, not acted on")
    parser.add_argument("--auto", action="store_true", help="unattended permission policy; recorded, not enforced")
    parser.add_argument("--format", default=None, help="accepted; the mock always emits its JSON event stream")
    return parser


def _emit_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="mock-opencode emit", description=_EMIT_DESCRIPTION, add_help=True)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--lever", dest="levers", action="append", default=[], metavar="NAME")
    parser.add_argument("--version", default="1.18.25")
    parser.add_argument("--auth-read-marker", default=None)
    parser.add_argument("--version-touch-path", default=None)
    return parser


def _run_export(argv: list[str]) -> int:
    """``mock-opencode export <session-id>`` — reads what
    :class:`OpenCodeTranscriptWriter` persisted (not ``opencode_surface``'s separate
    zipapp-only export), printing one JSON document to stdout on success. A missing or
    unreadable session fails loudly on stderr with a nonzero exit."""
    if len(argv) != 1:
        print("usage: mock-opencode export <session-id>", file=sys.stderr)
        return 2
    session_id = argv[0]
    env: Mapping[str, str] = os.environ
    cwd = acquired_worktree(env, Path.cwd())
    root = transcripts_root(env, fence_dir=fence_base_dir(cwd))
    path = document_path(root, session_id)
    try:
        raw = path.read_text()
    except OSError as exc:
        print(f"mock-opencode export: no such session {session_id!r} under {root}: {exc}", file=sys.stderr)
        return 1
    sys.stdout.write(raw)
    return 0


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


#: Flags that take a value, vs. bare boolean ones — pulled out before argparse sees them.
_VALUE_FLAGS = ("--session", "--model", "--variant", "--format")
_BOOL_FLAGS = ("--auto", "-h", "--help")


def _positionals_first(argv: list[str]) -> list[str]:
    """Reorder ``argv`` so ``subcommand`` and ``prompt`` are always adjacent — real flags
    between them (matching the real ``opencode`` CLI's shape) resolve wrong pre-3.13
    (gh-103372): argparse matches two ``nargs='?'`` positionals per contiguous run, so on
    3.12 the first run alone satisfies both and ``prompt`` is reported unrecognized."""
    options: list[str] = []
    positionals: list[str] = []
    i = 0
    while i < len(argv):
        token = argv[i]
        if token in _VALUE_FLAGS:
            options.append(token)
            if i + 1 < len(argv):
                options.append(argv[i + 1])
                i += 1
        elif token in _BOOL_FLAGS:
            options.append(token)
        else:
            positionals.append(token)
        i += 1
    return positionals + options


def _takeover_banner(session_id: str) -> str:
    """What a human would see pasted into their terminal — never parsed by anything;
    the real command's own composition (``resume_command``) is what blizzard's tests
    exercise, this just proves invoking the equivalent shape does not crash."""
    return f"mock-opencode: interactive takeover of session {session_id} (not automated)\n"


def _build_transcript_factory(*, cwd: Path, env: Mapping[str, str]) -> Callable[[str], ITranscriptWriter]:
    """A per-run factory OpenCode's self-minted session id can be handed to once it is
    known (``run_prompt``'s ``transcript_factory``) — unlike Claude Code, whose facade
    always sees the id up front (``claude_code.py``'s ``_build_transcript_writer``),
    OpenCode mints its own inside the engine, well before any facade code sees it."""
    root = transcripts_root(env, fence_dir=fence_base_dir(cwd))
    return lambda session_id: OpenCodeTranscriptWriter(session_id=session_id, root=root, cwd=cwd)


def main(argv: list[str] | None = None) -> None:
    """Entry point for the ``mock-opencode`` binary.

    ``emit``/``export`` are intercepted here, before any of ``run``'s own
    argparse/dispatch runs — ``run``'s path below is untouched from before either
    existed."""
    args_list = sys.argv[1:] if argv is None else list(argv)
    if args_list and args_list[0] == "--version":
        # The health probe (blizzard#438) shells out to this same binary, not only
        # `emit`'s separate CLI-surface artifact — so `run` must answer `--version` too.
        print(_MOCK_VERSION)
        raise SystemExit(0)
    if args_list and args_list[0] == "emit":
        raise SystemExit(_run_emit(args_list[1:]))
    if args_list and args_list[0] == "export":
        raise SystemExit(_run_export(args_list[1:]))
    if args_list and args_list[0] in ("-h", "--help"):
        print(_USAGE + _EMIT_MENTION)
        raise SystemExit(0)

    args = _parser().parse_args(_positionals_first(args_list))
    if args.subcommand not in ("run", None):
        print(_USAGE, file=sys.stderr)
        raise SystemExit(2)

    if args.subcommand is None and args.session and args.prompt is None:
        # The interactive TUI shape (``opencode --session <id> [--model][--variant]``,
        # no ``run``, no prompt): a human paste target, not an automated turn.
        sys.stdout.write(_takeover_banner(args.session))
        raise SystemExit(0)

    script = _common.read_script(args.prompt)
    if script is None:
        print(_USAGE)
        raise SystemExit(0)

    is_resume = args.session is not None
    wire = OpenCodeRunWire()
    env: Mapping[str, str] = os.environ
    cwd = acquired_worktree(env, Path.cwd())
    code = _common.dispatch(
        wire=wire,
        script=script,
        session_id=args.session,
        is_resume=is_resume,
        transcript_factory=_build_transcript_factory(cwd=cwd, env=env),
        # Recorded onto the session state (issue #144, blizzard#343), never acted on —
        # the mock is model-agnostic and never enforces a permission policy.
        model=args.model,
        effort=args.variant,
        permission="auto" if args.auto else None,
    )
    raise SystemExit(code)


if __name__ == "__main__":
    main()
