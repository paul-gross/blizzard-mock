"""Composes one runner-store ``local_pause_facts`` row — the runner's own brake,
engaged, stopping the daemon from claiming a mirrored fleet's ``ready`` chunks.

Distinct from the hub-store fleet brake (``domain/hub/runner_pause_seed.py``); this
table carries no ``reason`` column.
"""

from __future__ import annotations

from datetime import datetime

from blizzard_mock.mock_data.domain.facts import FactRow


def compose_local_pause(*, runner_id: str, set_at: datetime, set_by: str = "mock-data") -> FactRow:
    """One engaged ``local_pause_facts`` row — ``paused`` derives from the newest one."""
    return FactRow(
        table="local_pause_facts",
        values={"runner_id": runner_id, "paused": True, "set_at": set_at, "set_by": set_by},
    )
