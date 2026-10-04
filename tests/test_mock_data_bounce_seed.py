"""Unit coverage for the bounce composer (``blizzard-mock:unit-test``).

Pure, no store: ``compose_bounce`` is a plain function over already-loaded data.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest

from blizzard_mock.mock_data.domain.hub.bounce_seed import BounceCompositionError, compose_bounce

_NOW = datetime(2026, 1, 1, tzinfo=UTC)


def test_default_composes_a_conflict_bounce_with_a_minimal_envelope() -> None:
    row = compose_bounce(chunk_id="ch_1", epoch=2, recorded_at=_NOW)
    assert row.table == "chunk_bounces"
    assert row.values == {
        "chunk_id": "ch_1",
        "epoch": 2,
        "cause": "conflict",
        "envelope": json.dumps({"cause": "conflict"}),
        "recorded_at": _NOW,
    }


@pytest.mark.parametrize("cause", ["conflict", "checks", "master-moved"])
def test_every_schema_cause_is_accepted(cause: str) -> None:
    assert compose_bounce(chunk_id="ch_1", epoch=1, recorded_at=_NOW, cause=cause).values["cause"] == cause


def test_explicit_envelope_lands_verbatim() -> None:
    envelope = '{"reason": "rebase failed", "files": ["a.py"]}'
    row = compose_bounce(chunk_id="ch_1", epoch=1, recorded_at=_NOW, cause="checks", envelope=envelope)
    assert row.values["envelope"] == envelope


def test_unknown_cause_is_refused() -> None:
    with pytest.raises(BounceCompositionError, match="unknown cause"):
        compose_bounce(chunk_id="ch_1", epoch=1, recorded_at=_NOW, cause="bogus")


@pytest.mark.parametrize("envelope", ["not json", "[1, 2]", '"text"'])
def test_envelope_that_is_not_a_json_object_is_refused(envelope: str) -> None:
    with pytest.raises(BounceCompositionError, match="envelope"):
        compose_bounce(chunk_id="ch_1", epoch=1, recorded_at=_NOW, envelope=envelope)
