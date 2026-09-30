"""Unit coverage for the lease-fact composer (``blizzard-mock:unit-test``).

Pure, no store: ``compose_lease_row`` is a plain function over already-loaded data
(``bzh:domain-takes-objects``) — the same row shape ``domain/hub/chunk_seed.py``'s
``running``/``delivering`` statuses compose internally.
"""

from __future__ import annotations

from datetime import UTC, datetime

from blizzard_mock.mock_data.domain.hub.lease_seed import compose_lease_row, compose_lease_rows

_NOW = datetime(2026, 1, 1, tzinfo=UTC)


def test_compose_lease_row_lands_the_supplied_fields() -> None:
    row = compose_lease_row(chunk_id="ch_1", epoch=3, runner_id="r-1", minted_at=_NOW)
    assert row.table == "lease_facts"
    assert row.values == {"chunk_id": "ch_1", "epoch": 3, "runner_id": "r-1", "minted_at": _NOW}


def test_compose_lease_rows_records_the_epochs_runner_owner() -> None:
    lease, owner = compose_lease_rows(chunk_id="ch_1", epoch=3, runner_id="r-1", minted_at=_NOW)
    assert lease == compose_lease_row(chunk_id="ch_1", epoch=3, runner_id="r-1", minted_at=_NOW)
    assert owner.table == "epoch_owners"
    assert owner.values == {"chunk_id": "ch_1", "epoch": 3, "runner_id": "r-1", "recorded_at": _NOW}


def test_a_hub_mint_is_hub_owned() -> None:
    _lease, owner = compose_lease_rows(chunk_id="ch_1", epoch=2, runner_id="hub", minted_at=_NOW)
    assert owner.values["runner_id"] is None
