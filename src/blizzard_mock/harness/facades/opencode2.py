"""Mock OpenCode 2 facade (``mock-opencode-2``).

One verb today: ``emit`` bakes a lever set into a standalone CLI-surface zipapp (``opencode2_surface`` — see
``harness/README.md``'s "OpenCode 2 CLI-surface mode")."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from blizzard_mock.harness.opencode2_surface import emit as surface_emit
from blizzard_mock.harness.opencode2_surface import levers as surface_levers

_DESCRIPTION = (
    "Write a standalone CLI-surface fake-OpenCode-2 zipapp: it reports the baked version, registers and answers as "
    "the managed service with corpus-shaped /api/* responses, and never executes a caller-supplied prompt. See "
    "blizzard_mock.harness.opencode2_surface.levers.CATALOG for the lever roster."
)


def _emit_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="mock-opencode-2 emit", description=_DESCRIPTION, add_help=True)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--lever", dest="levers", action="append", default=[], metavar="NAME")
    parser.add_argument("--version", default=surface_emit.DEFAULT_VERSION)
    return parser


def _run_emit(argv: list[str]) -> int:
    """Validate the whole lever set before anything touches the filesystem, then write the zipapp."""
    args = _emit_parser().parse_args(argv)
    try:
        levers = surface_levers.parse(args.levers)
    except ValueError as exc:
        print(f"mock-opencode-2 emit: {exc}", file=sys.stderr)
        return 2
    surface_emit.emit(args.out, levers=levers, version=args.version)
    return 0


def main(argv: list[str] | None = None) -> None:
    args = sys.argv[1:] if argv is None else list(argv)
    if args and args[0] == "emit":
        raise SystemExit(_run_emit(args[1:]))
    print("usage: mock-opencode-2 emit --out <path> [--version <v>] [--lever NAME]...", file=sys.stderr)
    raise SystemExit(0 if args in ([], ["-h"], ["--help"]) else 2)


if __name__ == "__main__":
    main()
