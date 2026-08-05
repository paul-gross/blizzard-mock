"""Composes one runner-pause fact — the fleet's own brake (``runner_pause_facts``)
or the runner's own local brake (``runner_local_pause_facts``).

``runner_pause_facts`` has no ``reason`` column; supplying one fails loud
(pinned by tests/test_mock_data_runner_pause_seed.py).
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
