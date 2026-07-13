"""The shared mock-harness exec engine (*the prompt is the program*).

Stub. The Build step fills this in. All three per-harness facades call into this
one engine; they differ only in their CLI/wire surface.

Responsibilities the Build step wires here:

- **The fence.** Refuse to run unless test scaffolding marks the environment, so
  the mock can never pass as a real harness binding. ``assert_fenced()`` is the
  guard the facades call before doing anything.
- **The exec.** Treat the received prompt as Python source and ``exec()`` it in
  the acquired worktree with the spawn environment, exposing the
  ``blizzard_mock.harness.helpers`` surface to the script.
- **Session state.** Persist a per-session state file so a resumed script can
  read what it asked and act on the answer it was resumed with.
"""

from __future__ import annotations

#: Environment variable the test scaffolding sets to unfence the engine. The
#: real value/handshake is defined by the Build step; named here so the fence
#: contract has one owner.
FENCE_ENV_VAR = "BLIZZARD_MOCK_HARNESS_FENCE"


def assert_fenced() -> None:
    """Refuse to run unless the environment is marked as test scaffolding.

    Stub — the Build step implements the real handshake against
    :data:`FENCE_ENV_VAR`. Kept as an explicit seam so every facade guards
    through one function.
    """
    raise NotImplementedError("harness exec engine fence not implemented yet (Build step)")


def run_prompt(prompt: str) -> int:
    """Execute a behavior-script ``prompt`` and return the process exit code.

    Stub — the Build step implements the fenced ``exec()`` in the acquired
    worktree with session state.
    """
    raise NotImplementedError("harness exec engine not implemented yet (Build step)")
