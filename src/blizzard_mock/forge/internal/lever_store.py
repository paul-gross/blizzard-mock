"""In-memory lever store (``ILeverStore``).

Holds the active *state* levers, keyed by ``(kind, scope)``. Action levers are
never stored — they fire and mutate git/thread state at arm time. Process-local,
same lifetime as the forge process.
"""

from __future__ import annotations

from blizzard_mock.forge.domain.levers import Lever, LeverKind


class InMemoryLeverStore:
    """Process-local active-lever set. Implements ``ILeverStore``."""

    def __init__(self) -> None:
        self._levers: dict[tuple[LeverKind, str], Lever] = {}

    def arm(self, lever: Lever) -> None:
        self._levers[lever.kind, lever.scope_key()] = lever

    def clear(self, kind: LeverKind, repo: str | None, number: int | None) -> None:
        self._levers.pop((kind, Lever(kind=kind, repo=repo, number=number).scope_key()), None)

    def clear_all(self) -> None:
        self._levers.clear()

    def active(self) -> list[Lever]:
        return list(self._levers.values())

    def find(self, kind: LeverKind, repo: str | None, number: int | None) -> Lever | None:
        for lever in self._levers.values():
            if lever.kind is kind and lever.matches(repo, number):
                return lever
        return None

    def consume(self, lever: Lever) -> None:
        if lever.remaining is None:
            return
        lever.remaining -= 1
        if lever.remaining <= 0:
            self._levers.pop((lever.kind, lever.scope_key()), None)
