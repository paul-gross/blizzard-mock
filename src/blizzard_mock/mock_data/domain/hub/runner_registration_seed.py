"""Composes one hub-store ``runner_registrations`` row — a runner the hub added under
its minted id and a display name, either registered or added but never connected.

The name is a label, not a key: two rows may share one, and every other runner-keyed
table references ``runner_id``.
"""

from __future__ import annotations

from datetime import datetime

from blizzard_mock.mock_data.domain.facts import FactRow

#: The display name :data:`~blizzard_mock.mock_data.domain.ids.SEED_RUNNER_ID` is added under.
SEED_RUNNER_NAME = "runner-seed"

_ADDED_BY = "mock-data"


def compose_runner_registration(
    *,
    runner_id: str,
    name: str,
    added_at: datetime,
    workspace_id: str | None,
    subscriptions: str | None = None,
) -> FactRow:
    """One ``runner_registrations`` row added at ``added_at``. A ``workspace_id`` is the
    binding of a runner that registered then and was last seen then; ``None`` composes
    one added but never connected, every connection column NULL. ``subscriptions`` is the
    declared roster's JSON (``runner_subscription_seed.compose_declared_roster``); ``None``
    stores no roster."""
    registered_at = added_at if workspace_id is not None else None
    return FactRow(
        table="runner_registrations",
        values={
            "runner_id": runner_id,
            "name": name,
            "added_at": added_at,
            "added_by": _ADDED_BY,
            "workspace_id": workspace_id,
            "registered_at": registered_at,
            "last_seen_at": registered_at,
            "subscriptions": subscriptions,
        },
    )
