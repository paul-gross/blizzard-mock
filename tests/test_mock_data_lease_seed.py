"""Unit coverage for the lease-fact composer (``blizzard-mock:unit-test``).

Pure, no store: ``compose_lease_row`` is a plain function over already-loaded data
(``bzh:domain-takes-objects``) — the same row shape ``domain/hub/chunk_seed.py``'s
``running``/``delivering`` statuses compose internally.
"""

from __future__ import annotations

from datetime import UTC, datetime

from blizzard_mock.mock_data.domain.hub.lease_seed import compose_lease_row

_NOW = datetime(2026, 1, 1, tzinfo=UTC)


def test_compose_lease_row_lands_the_supplied_fields() -> None:
    row = compose_lease_row(chunk_id="ch_1", epoch=3, runner_id="r-1", minted_at=_NOW)
    assert row.table == "lease_facts"
    assert row.values == {"chunk_id": "ch_1", "epoch": 3, "runner_id": "r-1", "minted_at": _NOW}
