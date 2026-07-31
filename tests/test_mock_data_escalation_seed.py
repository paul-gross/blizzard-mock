"""Unit coverage for the escalation composer (``blizzard-mock:unit-test``).

Pure, no store: ``compose_escalation`` is a plain function over already-loaded data
(``bzh:domain-takes-objects``).
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from blizzard_mock.mock_data.domain.escalation_seed import EscalationCompositionError, compose_escalation

_NOW = datetime(2026, 1, 1, tzinfo=UTC)


def test_default_cause_composes_the_generic_resume_placeholder() -> None:
    row = compose_escalation(chunk_id="ch_1", epoch=1, recorded_at=_NOW)
    assert row.table == "escalations"
    assert row.values["takeover_command"] == "cd <workdir> && <resume ch_1>"
    assert row.values["chunk_id"] == "ch_1"
    assert row.values["epoch"] == 1
    assert row.values["decision_id"] is None
    assert row.values["recorded_at"] == _NOW


def test_cause_retries_is_the_same_as_the_default() -> None:
    row = compose_escalation(chunk_id="ch_1", epoch=1, recorded_at=_NOW, cause="retries")
    assert row.values["takeover_command"] == "cd <workdir> && <resume ch_1>"


def test_cause_cap_composes_the_recognizable_spend_cap_wording() -> None:
    row = compose_escalation(chunk_id="ch_1", epoch=1, recorded_at=_NOW, cause="cap")
    takeover = row.values["takeover_command"]
    assert isinstance(takeover, str)
    assert takeover.startswith("spend cap ")
    assert "reached" in takeover
    assert takeover.endswith("cd <workdir> && <resume ch_1>")


def test_explicit_takeover_command_overrides_either_cause_default() -> None:
    row = compose_escalation(chunk_id="ch_1", epoch=1, recorded_at=_NOW, cause="cap", takeover_command="custom")
    assert row.values["takeover_command"] == "custom"


def test_unknown_cause_is_refused() -> None:
    with pytest.raises(EscalationCompositionError, match="unknown cause"):
        compose_escalation(chunk_id="ch_1", epoch=1, recorded_at=_NOW, cause="bogus")


def test_decision_id_is_carried_through() -> None:
    row = compose_escalation(chunk_id="ch_1", epoch=1, recorded_at=_NOW, decision_id="dec_1")
    assert row.values["decision_id"] == "dec_1"
