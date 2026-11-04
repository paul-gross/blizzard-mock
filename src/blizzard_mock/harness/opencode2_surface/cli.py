"""The CLI-surface artifact's entry point: the shared OpenCode 2 verbs, each turn answered by its fixed planner."""

from __future__ import annotations

import os
import sys

from ..opencode2_core.cli import USAGE, dispatch
from . import _baked
from .internal.turns import PlannedTurns


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else list(argv)
    answered = dispatch(
        args, env=os.environ, argv0=sys.argv[0], levers=_baked.LEVERS, version=_baked.VERSION, runner=PlannedTurns()
    )
    if answered is not None:
        return answered
    print(f"usage: {USAGE}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
