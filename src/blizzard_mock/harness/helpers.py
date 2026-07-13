"""The terse helper library behavior scripts import.

A mock-harness prompt *is* a Python script; these helpers keep the common cases
one line each, with raw Python underneath for the weird cases. Stubs — the Build
step implements them against :mod:`blizzard_mock.harness.engine`'s session state
and spawn environment.

Everything downstream of the harness seam runs for real: ``commit`` makes real
git commits, ``ask`` fires the real ask/answer protocol and parks the session.
"""

from __future__ import annotations


def ask(question: str) -> None:
    """Fire the ask/answer protocol (``blizzard ask``) and exit, parking the session.

    On resume, the answer arrives as the next prompt (again as code); the
    persisted session state lets the resumed script see what it asked.
    """
    raise NotImplementedError("harness helper ask() not implemented yet (Build step)")


def apply_diff(diff: str) -> None:
    """Apply a unified ``diff`` to the acquired worktree."""
    raise NotImplementedError("harness helper apply_diff() not implemented yet (Build step)")


def commit(message: str) -> None:
    """Make a real ``git commit`` in the acquired worktree."""
    raise NotImplementedError("harness helper commit() not implemented yet (Build step)")


def verdict(value: str) -> None:
    """Emit a completion verdict in the facade's expected output format."""
    raise NotImplementedError("harness helper verdict() not implemented yet (Build step)")


def hang() -> None:
    """Block forever, so the runner's stall/heartbeat handling can be exercised."""
    raise NotImplementedError("harness helper hang() not implemented yet (Build step)")


def crash() -> None:
    """Terminate abnormally, so the runner's crash handling can be exercised."""
    raise NotImplementedError("harness helper crash() not implemented yet (Build step)")
