"""Composes a declared roster's JSON encoding, and the per-slug external-usage
sample/miss rows that sit beside it.

No FK: neither ``runner_external_usage`` nor ``runner_external_usage_misses`` carries
one to ``runner_registrations``, so these rows compose independently of the roster.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from datetime import datetime, timedelta

from blizzard_mock.mock_data.domain.facts import FactRow

_DEFAULT_WINDOW_SECONDS = 18000


def compose_declared_roster(subscriptions: Sequence[tuple[str, str, str]]) -> str:
    """The ``runner_registrations.subscriptions`` column's JSON encoding — one
    ``{slug, name, provider}`` object per declared ``(slug, name, provider)`` entry, in
    the exact shape the hub store reads back."""
    return json.dumps([{"slug": slug, "name": name, "provider": provider} for slug, name, provider in subscriptions])


def _default_windows(sampled_at: datetime) -> list[dict[str, object]]:
    resets_at = sampled_at + timedelta(seconds=_DEFAULT_WINDOW_SECONDS)
    return [
        {
            "window": "5h",
            "utilization_pct": 42.0,
            "resets_at": resets_at.isoformat(),
            "window_seconds": _DEFAULT_WINDOW_SECONDS,
        }
    ]


def compose_subscription_sample(
    *,
    runner_id: str,
    slug: str,
    name: str,
    sampled_at: datetime,
    windows: list[dict[str, object]] | None = None,
) -> FactRow:
    """One ``runner_external_usage`` row — a declared subscription's newest sample."""
    return FactRow(
        table="runner_external_usage",
        values={
            "runner_id": runner_id,
            "slug": slug,
            "name": name,
            "sampled_at": sampled_at,
            "windows": json.dumps(windows if windows is not None else _default_windows(sampled_at)),
            "updated_at": sampled_at,
        },
    )


def compose_subscription_miss(*, runner_id: str, slug: str, name: str, missed_at: datetime, reason: str) -> FactRow:
    """One ``runner_external_usage_misses`` row — a declared subscription's newest miss."""
    return FactRow(
        table="runner_external_usage_misses",
        values={
            "runner_id": runner_id,
            "slug": slug,
            "name": name,
            "missed_at": missed_at,
            "reason": reason,
            "updated_at": missed_at,
        },
    )
