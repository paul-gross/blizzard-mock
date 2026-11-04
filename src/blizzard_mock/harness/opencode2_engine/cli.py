"""The engine-backed artifact's entry point: the shared OpenCode 2 verbs, each turn run as a script, and the internal
``turn`` verb that script's child process runs under."""

from __future__ import annotations

import os
import sys

from ..opencode2_core.cli import USAGE, dispatch
from . import _baked
from .internal.turns import ScriptedTurns
from .turn import runner


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else list(argv)
    if args == ["turn"]:
        return runner.main()
    argv0 = os.path.abspath(sys.argv[0])
    answered = dispatch(
        args, env=os.environ, argv0=argv0, levers=_baked.LEVERS, version=_baked.VERSION, runner=ScriptedTurns(argv0)
    )
    if answered is not None:
        return answered
    print(f"usage: {USAGE}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
