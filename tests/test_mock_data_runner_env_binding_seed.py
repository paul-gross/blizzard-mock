"""Unit coverage for the runner-store env-binding composer (``blizzard-mock:unit-test``).

Pure, no store: ``compose_env_binding`` is a plain function over already-loaded data
(``bzh:domain-takes-objects``).
"""

from __future__ import annotations

from datetime import UTC, datetime

from blizzard_mock.mock_data.domain.runner.env_binding_seed import compose_env_binding

_NOW = datetime(2026, 1, 1, tzinfo=UTC)


def test_compose_env_binding_lands_the_supplied_fields() -> None:
    row = compose_env_binding(chunk_id="ch_1", environment_id="env-1", workdir="/work/ch_1", bound_at=_NOW)
    assert row.table == "env_bindings"
    assert row.values == {
        "chunk_id": "ch_1",
        "environment_id": "env-1",
        "workdir": "/work/ch_1",
        "bound_at": _NOW,
    }
