"""Composes one runner-store ``local_pause_facts`` row — the runner's own brake,
engaged, stopping the daemon from claiming a mirrored fleet's ``ready`` chunks.

Distinct from the hub-store fleet-brake mirror (``domain/hub/runner_pause_seed.py``),
which carries its own, separate ``reason`` column on ``runner_local_pause_facts``.
"""

from __future__ import annotations

from datetime import datetime

from blizzard_mock.mock_data.domain.facts import FactRow


def compose_local_pause(
    *, runner_id: str, set_at: datetime, set_by: str = "mock-data", reason: str | None = None
) -> FactRow:
    """One engaged ``local_pause_facts`` row — ``paused`` derives from the newest one.
    ``reason`` names the cause (a usage limit, the spend ceiling), or ``None`` for a plain
    operator pause (blizzard#594) — mirrors the real brake's own ``PauseService.engage``."""
    return FactRow(
        table="local_pause_facts",
        values={"runner_id": runner_id, "paused": True, "set_at": set_at, "set_by": set_by, "reason": reason},
    )
