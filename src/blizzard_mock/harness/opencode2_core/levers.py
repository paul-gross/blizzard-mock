"""The service-level lever vocabulary an emitted OpenCode 2 artifact bakes in, immutable once emitted."""

from __future__ import annotations

from collections.abc import Iterable
from enum import StrEnum


class Lever(StrEnum):
    """One member per service-level misbehaviour — see :data:`CATALOG` for prose. The values are the
    ``--lever NAME`` strings ``blizzard``'s service tests hardcode as a public CLI contract."""

    NEVER_REGISTERED = "NEVER_REGISTERED"
    NEVER_ANSWERS = "NEVER_ANSWERS"
    VERSION_MISMATCH = "VERSION_MISMATCH"
    EVENTS_DROPPED = "EVENTS_DROPPED"
    INTERRUPT_IGNORED = "INTERRUPT_IGNORED"
    RESTARTS_MID_TURN = "RESTARTS_MID_TURN"
    DISTORTED_SHAPES = "DISTORTED_SHAPES"
    UNKNOWN_PART_TYPES = "UNKNOWN_PART_TYPES"
    NO_PRICES = "NO_PRICES"


CATALOG: dict[str, str] = {
    Lever.NEVER_REGISTERED.value: "`service start` exits 0 but no service ever registers",
    Lever.NEVER_ANSWERS.value: "`service start` registers an address nothing answers at",
    Lever.VERSION_MISMATCH.value: "the service reports a different version than `--version`",
    Lever.EVENTS_DROPPED.value: "the event stream closes as soon as it opens",
    Lever.INTERRUPT_IGNORED.value: "an interrupt answers `interrupted` and the turn runs on",
    Lever.RESTARTS_MID_TURN.value: "the service restarts itself once, while its first shell tool runs",
    Lever.DISTORTED_SHAPES.value: "the message list answers an object instead of a list",
    Lever.UNKNOWN_PART_TYPES.value: "assistant tool parts carry an unknown part type",
    Lever.NO_PRICES.value: "the model listing carries no prices, as under a subscription login",
}


def parse(names: Iterable[str]) -> frozenset[Lever]:
    """Validate ``names`` against :class:`Lever`; raise naming every bad one."""
    given = list(names)
    valid = {member.value: member for member in Lever}
    bad = sorted({name for name in given if name not in valid})
    if bad:
        raise ValueError(f"unknown opencode 2 lever name(s): {', '.join(bad)}")
    return frozenset(valid[name] for name in given)


__all__ = ["CATALOG", "Lever", "parse"]
