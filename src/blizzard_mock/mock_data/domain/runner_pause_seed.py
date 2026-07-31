"""Composes one runner-pause fact — the fleet's own brake (``runner_pause_facts``) or
the runner's own local brake (``runner_local_pause_facts``), two separate tables with
separate authors (``blizzard/hub/store/schema.py``'s module comment above each).

``runner_pause_facts`` carries **no ``reason`` column** — only
``runner_local_pause_facts`` does (nullable there; issue #61's spend-ceiling brake
composes ``f"spend ceiling ${cap:.2f} reached over the trailing {window_hours:g}h
(spend ${spend:.2f})"`` into it, ``blizzard.runner.loop.steps.check_spend_ceiling``).
Supplying a reason for the fleet brake is a genuine schema mismatch, not a detail to
drop silently — :func:`compose_runner_pause` fails loud naming the missing column,
never a silently-dropped value.
"""

from __future__ import annotations

from datetime import datetime

from blizzard_mock.mock_data.domain.facts import FactRow

_DEFAULT_SET_BY = "mock-data"


class RunnerPauseCompositionError(Exception):
    """A ``--reason`` paired with ``--fleet`` — the fleet brake has no such column."""


def compose_runner_pause(
    *,
    runner_id: str,
    local: bool,
    reason: str | None = None,
    set_at: datetime,
    set_by: str = _DEFAULT_SET_BY,
) -> FactRow:
    """One pause fact — ``runner_local_pause_facts`` when ``local``, else
    ``runner_pause_facts``. Raises :class:`RunnerPauseCompositionError` when ``reason``
    is supplied for the fleet brake (``local=False``), which has no ``reason`` column."""
    if not local and reason is not None:
        raise RunnerPauseCompositionError(
            "runner_pause_facts (the fleet brake, --fleet) has no `reason` column — "
            "only runner_local_pause_facts (--local) carries one"
        )
    if local:
        return FactRow(
            table="runner_local_pause_facts",
            values={"runner_id": runner_id, "paused": True, "set_at": set_at, "set_by": set_by, "reason": reason},
        )
    return FactRow(
        table="runner_pause_facts",
        values={"runner_id": runner_id, "paused": True, "set_at": set_at, "set_by": set_by},
    )
