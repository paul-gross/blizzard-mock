"""Composes one event row — into a tenant's ``event_log``, or the hub's own ``hub_event_log``.

Composes exactly one row, never a redundant one for an escalation (pinned by
tests/test_mock_data_event_seed.py). ``detail`` must parse as JSON before
composing — fail loud, never a silently-malformed row.

An occurrence of the hub's own machinery concerns no tenant, so it never lands in a
tenant's ``event_log``: a hub-level kind naming no runner, chunk, lease or node is a
``hub_event_log`` row. An export kind that names one is a runner's report of its own
export and stays in its tenant's log; any other hub-level kind naming one is refused.
"""

from __future__ import annotations

import json
from datetime import datetime

from blizzard_mock.mock_data.domain.facts import FactRow

INFO = "info"
WARNING = "warning"
CRITICAL = "critical"

#: The three severities ``event_log.severity`` accepts, mirrored independently
#: (no ``blizzard`` import).
SEVERITIES = (INFO, WARNING, CRITICAL)

#: A tenant's operational event feed.
EVENT_LOG_TABLE = "event_log"
#: The hub's own occurrences, which concern no tenant.
HUB_EVENT_LOG_TABLE = "hub_event_log"

#: The trace and egress exports' kinds — hub-level, though a runner also reports its own
#: trace export's into its tenant's log.
EXPORT_EVENT_KINDS: frozenset[str] = frozenset(
    {
        "trace-export-failed",
        "trace-export-recovered",
        "trace-window-skipped",
        "trace-config-rejected",
        "egress-write-failed",
        "egress-write-recovered",
        "egress-config-rejected",
        "egress-cursor-reset",
    }
)

#: Every hub-level kind, kept in step by hand with the hub's ``HubEventKind``: the exports',
#: the hub's startup and migration, and a tenant teardown's close and completion.
HUB_EVENT_KINDS: frozenset[str] = EXPORT_EVENT_KINDS | {
    "hub-started",
    "hub-migrated",
    "tenant-closed",
    "tenant-deleted",
}


class EventCompositionError(Exception):
    """A ``--severity``/``--detail`` ``compose_event`` cannot honor, or a hub-level kind
    that names a runner, chunk, lease or node it can never concern."""


def is_hub_event_kind(kind: str) -> bool:
    """Whether ``kind`` is an occurrence of the hub's own machinery."""
    return kind in HUB_EVENT_KINDS


def compose_event(
    *,
    kind: str,
    severity: str,
    message: str,
    runner_id: str | None,
    chunk_id: str | None = None,
    lease_id: str | None = None,
    node_name: str | None = None,
    detail: str | None = None,
    recorded_at: datetime,
) -> FactRow:
    """One ``event_log`` row, or one ``hub_event_log`` row for a hub-level ``kind`` that
    names no runner, chunk, lease or node. Raises :class:`EventCompositionError` for an
    unknown ``severity``, a ``detail`` that does not parse as JSON, or a hub-level kind
    other than an export's that names any of them."""
    if severity not in SEVERITIES:
        raise EventCompositionError(f"unknown severity {severity!r} — one of {SEVERITIES}")
    if detail is not None:
        try:
            json.loads(detail)
        except json.JSONDecodeError as exc:
            raise EventCompositionError(f"--detail is not valid JSON: {exc}") from exc
    named = {
        name: value
        for name, value in (("runner", runner_id), ("chunk", chunk_id), ("lease", lease_id), ("node", node_name))
        if value is not None
    }
    if is_hub_event_kind(kind) and not named:
        return FactRow(
            table=HUB_EVENT_LOG_TABLE,
            values={
                "recorded_at": recorded_at,
                "severity": severity,
                "kind": kind,
                "message": message,
                "detail": detail,
            },
        )
    if is_hub_event_kind(kind) and kind not in EXPORT_EVENT_KINDS:
        raise EventCompositionError(
            f"{kind!r} is the hub's own occurrence, recorded in {HUB_EVENT_LOG_TABLE} alone — it names no "
            f"runner, chunk, lease or node, yet this one names its {', '.join(named)}"
        )
    return FactRow(
        table=EVENT_LOG_TABLE,
        values={
            "recorded_at": recorded_at,
            "severity": severity,
            "kind": kind,
            "runner_id": runner_id,
            "chunk_id": chunk_id,
            "lease_id": lease_id,
            "node_name": node_name,
            "message": message,
            "detail": detail,
        },
    )
