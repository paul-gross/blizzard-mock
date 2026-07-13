"""Mock Claude Code facade (``mock-claude-code``).

Stub: prints usage and exits 0. The Build step replaces this with a real CLI
surface mimicking Claude Code's invocation shape, output format, exit behavior,
and resume semantics, delegating the actual behavior to
:mod:`blizzard_mock.harness.engine` (which is fenced against non-test use).
"""

from __future__ import annotations

_USAGE = """\
mock-claude-code — mock Claude Code coding-harness facade (not yet implemented)

Intended contract:
  Present Claude Code's CLI/wire surface (invocation shape, output format, exit
  + resume semantics) to the runner's adapter, and turn the received prompt
  into behavior by exec()'ing it as Python in the acquired worktree — the
  prompt is the program.

  Fenced: refuses to run unless test scaffolding marks the environment, so it
  can never pass as a real harness binding.

See src/blizzard_mock/harness/README.md for the full contract.
"""


def main() -> None:
    """Print usage and exit 0. Build step wires the real facade."""
    print(_USAGE)
