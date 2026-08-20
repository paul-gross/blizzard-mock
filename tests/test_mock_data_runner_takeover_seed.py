"""Unit coverage for the runner-store takeover composer (``blizzard-mock:unit-test``).

Pure, no store: ``compose_takeover`` is a plain function over already-loaded data
(``bzh:domain-takes-objects``), minting its own ``tko_<ulid>`` id.
"""

from __future__ import annotations

from datetime import UTC, datetime

from blizzard_mock.clock import FixedClock
from blizzard_mock.mock_data.domain.ids import seeded_rng
from blizzard_mock.mock_data.domain.runner.takeover_seed import compose_takeover

_NOW = datetime(2026, 1, 1, tzinfo=UTC)


def test_compose_takeover_mints_a_prefixed_id() -> None:
    row = compose_takeover(
        chunk_id="ch_1",
        lease_id="lease_1",
        session_id="sess_1",
        workdir="/work/ch_1",
        fence_epoch=None,
        opened_at=_NOW,
        clock=FixedClock(_NOW),
        rng=seeded_rng(1),
    )
    assert row.table == "takeovers"
    assert str(row.values["takeover_id"]).startswith("tko_")


def test_compose_takeover_lands_a_null_fence_epoch_when_not_live() -> None:
    row = compose_takeover(
        chunk_id="ch_1",
        lease_id="lease_1",
        session_id="sess_1",
        workdir="/work/ch_1",
        fence_epoch=None,
        opened_at=_NOW,
        clock=FixedClock(_NOW),
        rng=seeded_rng(1),
    )
    assert row.values["fence_epoch"] is None


def test_compose_takeover_same_seed_mints_the_same_id() -> None:
    first = compose_takeover(
        chunk_id="ch_1",
        lease_id="lease_1",
        session_id="sess_1",
        workdir="/work/ch_1",
        fence_epoch=None,
        opened_at=_NOW,
        clock=FixedClock(_NOW),
        rng=seeded_rng(7),
    )
    second = compose_takeover(
        chunk_id="ch_1",
        lease_id="lease_1",
        session_id="sess_1",
        workdir="/work/ch_1",
        fence_epoch=None,
        opened_at=_NOW,
        clock=FixedClock(_NOW),
        rng=seeded_rng(7),
    )
    assert first.values["takeover_id"] == second.values["takeover_id"]
