"""Composes one runner-store ``env_bindings`` row — the panel's Environments section.

Never composed bare: ``scenario_seed`` composes one alongside every mirrored lease.
"""

from __future__ import annotations

from datetime import datetime

from blizzard_mock.mock_data.domain.facts import FactRow


def compose_env_binding(*, chunk_id: str, environment_id: str, workdir: str, bound_at: datetime) -> FactRow:
    """One held ``env_bindings`` row — no ``binding_releases`` composed, so it reads
    as held (``HELD_BINDING``, ``sqlalchemy_store.py``)."""
    return FactRow(
        table="env_bindings",
        values={"chunk_id": chunk_id, "environment_id": environment_id, "workdir": workdir, "bound_at": bound_at},
    )
