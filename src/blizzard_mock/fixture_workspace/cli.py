"""Entrypoint for the fixture-workspace scaffold (``blizzard-mock-fixture``).

Stub: prints usage and exits 0. The Build step replaces this with the real
scaffold — mint bare ``file://`` origins and a real winter workspace under a
per-env scratch path, plus teardown.
"""

from __future__ import annotations

_USAGE = """\
blizzard-mock-fixture — fixture-workspace scaffold (not yet implemented)

Intended contract:
  Mint a real, disposable winter workspace under a per-env scratch path
  (keyed off WINTER_ENV): a directory of bare git origin repos as file://
  remotes, plus a real winter workspace initialized against them with a small
  committed history and a .winter/config.toml declaring them as project repos.

  The bare origins are the SAME repos the mock forge fronts (one git truth).

See src/blizzard_mock/fixture_workspace/README.md for the full contract.
"""


def main() -> None:
    """Print usage and exit 0. Build step wires the real scaffold."""
    print(_USAGE)
