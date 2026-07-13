"""Entrypoint for the mock GitHub forge service (``blizzard-mock-forge``).

Stub: prints usage and exits 0. The Build step replaces this with a real
uvicorn launch of the forge FastAPI app, bound to ``BZ_FORGE_PORT`` and backed
by the bare-repo directory.
"""

from __future__ import annotations

_USAGE = """\
blizzard-mock-forge — mock GitHub forge service (not yet implemented)

Intended contract:
  Serve the GitHub API subset blizzard touches (issues + comment threads;
  PRs + merges) over a directory of bare git repos, with a lever surface for
  edge states (external merge, conflict, rate-limit, unreachable, ...).

  Mergeability and merges are computed/performed against REAL refs in the bare
  repos the fixture-workspace pushes to — one git truth.

See src/blizzard_mock/forge/README.md for the full contract.
"""


def main() -> None:
    """Print usage and exit 0. Build step wires the real service."""
    print(_USAGE)
