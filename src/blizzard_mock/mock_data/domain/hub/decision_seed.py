"""Composes one gate decision's ``FactRow`` set — the ``decisions``/``decision_resolutions``
trail (``bzh:facts-not-status``).

A decision parks its chunk ``waiting_on_human`` while no closure fact names it; a
``decision_resolutions`` row records the picked choice without closing it — the
resolving transition does that.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import timedelta
from random import Random

from blizzard_mock.clock import Clock
from blizzard_mock.mock_data.domain import ids
from blizzard_mock.mock_data.domain.facts import FactRow


class DecisionCompositionError(Exception):
    """A ``--choice``/``--resolve``/``--resolved-by`` combination ``compose_decision`` cannot honor."""


@dataclass(frozen=True)
class DecisionChoice:
    """One selectable gate outcome."""

    name: str
    description: str = ""


@dataclass(frozen=True)
class DecisionSeed:
    """One composed decision: its minted id and the exact ``FactRow``\\ s to write."""

    decision_id: str
    rows: list[FactRow] = field(default_factory=list)


def compose_decision(
    *,
    chunk_id: str,
    node_id: str,
    node_name: str,
    clock: Clock,
    rng: Random,
    choices: Sequence[DecisionChoice],
    epoch: int = 1,
    imposed_by_runner_id: str | None = None,
    resolved_choice: str | None = None,
    resolved_by: str | None = None,
) -> DecisionSeed:
    """Compose one decision, optionally already resolved.

    ``choices`` must be non-empty with unique names; ``resolved_choice`` must name
    one of them, and ``resolved_choice``/``resolved_by`` come together or not at all.
    """
    if not choices:
        raise DecisionCompositionError("a decision needs at least one --choice")
    names = [choice.name for choice in choices]
    if len(set(names)) != len(names):
        raise DecisionCompositionError(f"--choice names must be unique, got {names!r}")
    if (resolved_choice is None) != (resolved_by is None):
        raise DecisionCompositionError("--resolve and --resolved-by must be supplied together, or neither")
    if resolved_choice is not None and resolved_choice not in names:
        raise DecisionCompositionError(f"--resolve {resolved_choice!r} is not one of the --choice names {names!r}")

    minted_decision_id = ids.mint(ids.DECISION_PREFIX, clock, rng)
    now = clock.now()
    rows: list[FactRow] = [
        FactRow(
            table="decisions",
            values={
                "decision_id": minted_decision_id,
                "chunk_id": chunk_id,
                "node_id": node_id,
                "node_name": node_name,
                "epoch": epoch,
                "choices": json.dumps([{"name": c.name, "description": c.description} for c in choices]),
                "submitted_at": now,
                "imposed_by_runner_id": imposed_by_runner_id,
            },
        )
    ]
    if resolved_choice is not None:
        rows.append(
            FactRow(
                table="decision_resolutions",
                values={
                    "decision_id": minted_decision_id,
                    "choice": resolved_choice,
                    "resolved_by": resolved_by,
                    "resolved_at": now + timedelta(seconds=1),
                },
            )
        )
    return DecisionSeed(decision_id=minted_decision_id, rows=rows)
