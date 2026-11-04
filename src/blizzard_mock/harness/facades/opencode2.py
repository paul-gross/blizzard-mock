"""Mock OpenCode 2 facade (``mock-opencode-2``).

Two verbs, each baking a lever set into a standalone zipapp: ``emit`` writes the CLI-surface artifact
(``opencode2_surface``), which answers a prompt's plan and never executes it; ``emit-engine`` writes the engine-backed
mock service (``opencode2_engine``), which runs each turn's prompt as a script in a child process. See
``harness/README.md``'s "OpenCode 2 CLI-surface mode" and "OpenCode 2 engine-backed mode" sections."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable
from pathlib import Path

from blizzard_mock.harness.opencode2_core import levers as core_levers
from blizzard_mock.harness.opencode2_core.emit import DEFAULT_VERSION
from blizzard_mock.harness.opencode2_engine import emit as engine_emit
from blizzard_mock.harness.opencode2_surface import emit as surface_emit

type _Emitter = Callable[..., None]

_ROSTER = "See blizzard_mock.harness.opencode2_core.levers.CATALOG for the lever roster."
_VERBS: dict[str, tuple[str, _Emitter]] = {
    "emit": (
        "Write a standalone CLI-surface fake-OpenCode-2 zipapp: it reports the baked version, registers and answers "
        "as the managed service with corpus-shaped /api/* responses, and never executes a caller-supplied prompt. "
        + _ROSTER,
        surface_emit.emit,
    ),
    "emit-engine": (
        "Write a standalone engine-backed fake-OpenCode-2 zipapp: the same managed service, but each turn's prompt "
        "is a Python script run in a child process of its own, under the mock harness fence. " + _ROSTER,
        engine_emit.emit,
    ),
}


def _parser(verb: str, description: str) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog=f"mock-opencode-2 {verb}", description=description, add_help=True)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--lever", dest="levers", action="append", default=[], metavar="NAME")
    parser.add_argument("--version", default=DEFAULT_VERSION)
    return parser


def _run(verb: str, argv: list[str]) -> int:
    """Validate the whole lever set before anything touches the filesystem, then write the zipapp."""
    description, emitter = _VERBS[verb]
    args = _parser(verb, description).parse_args(argv)
    try:
        levers = core_levers.parse(args.levers)
    except ValueError as exc:
        print(f"mock-opencode-2 {verb}: {exc}", file=sys.stderr)
        return 2
    emitter(args.out, levers=levers, version=args.version)
    return 0


def main(argv: list[str] | None = None) -> None:
    args = sys.argv[1:] if argv is None else list(argv)
    if args and args[0] in _VERBS:
        raise SystemExit(_run(args[0], args[1:]))
    print("usage: mock-opencode-2 emit|emit-engine --out <path> [--version <v>] [--lever NAME]...", file=sys.stderr)
    raise SystemExit(0 if args in ([], ["-h"], ["--help"]) else 2)


if __name__ == "__main__":
    main()
