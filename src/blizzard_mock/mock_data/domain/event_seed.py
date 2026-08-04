"""Composes one ``event_log`` row — the operational event feed
(``blizzard/hub/store/schema.py``, issue #125, ``bzh:facts-not-status``).

Independent of ``escalations`` by design: the hub already synthesizes an open escalation
into the read-time event feed, so this module composes exactly one ``event_log`` row and
never a redundant one for an escalation (pinned by tests/test_mock_data_event_seed.py::
test_compose_event_lands_the_supplied_fields).

``detail`` is opaque, round-tripped-only JSON on the real column — this module
validates it *parses* as JSON before composing (fail loud, never a silently-malformed
row) but never interprets its contents.
"""

from __future__ import annotations

import json
from datetime import datetime

from blizzard_mock.mock_data.domain.facts import FactRow

INFO = "info"
WARNING = "warning"
CRITICAL = "critical"

#: The three severities ``event_log.severity`` accepts (``blizzard.hub.domain.work``'s
#: own ``_SEVERITY_RANK`` vocabulary, independently mirrored — no ``blizzard`` import).
SEVERITIES = (INFO, WARNING, CRITICAL)


class EventCompositionError(Exception):
    """A ``--severity``/``--detail`` ``compose_event`` cannot honor."""


def compose_event(
    *,
    kind: str,
    severity: str,
    message: str,
    runner_id: str,
    chunk_id: str | None = None,
    lease_id: str | None = None,
    node_name: str | None = None,
    detail: str | None = None,
    recorded_at: datetime,
) -> FactRow:
    """One ``event_log`` row. Raises :class:`EventCompositionError` for an unknown
    ``severity`` or a ``detail`` that does not parse as JSON."""
    if severity not in SEVERITIES:
        raise EventCompositionError(f"unknown severity {severity!r} — one of {SEVERITIES}")
    if detail is not None:
        try:
            json.loads(detail)
        except json.JSONDecodeError as exc:
            raise EventCompositionError(f"--detail is not valid JSON: {exc}") from exc
    return FactRow(
        table="event_log",
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
