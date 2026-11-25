"""``reset --store hub`` against a store a real ``blizzard-hub init`` wrote — the guard that
the hub's reset boundary keeps every row a fresh init seeds (``blizzard-mock:unit-test``).

A tenant's starting state is whatever the hub's migrations write for it, which this repo's
synthetic schemas cannot know. So the store here comes from the sibling ``blizzard`` checkout's
own binary, and any row the reset drops fails the test, naming its table.

Its reach is the ``init`` path only. What creating a further tenant writes — the work-item
counter, covered by ``test_mock_data_reflected_store.py``, and the packaged graphs, which a
reset deletes — is not seen here."""

from __future__ import annotations

import os
import subprocess
from collections import Counter
from pathlib import Path

import pytest
from click.testing import CliRunner
from sqlalchemy import MetaData, create_engine, select

from blizzard_mock.mock_data.cli import cli

#: Reads the sibling ``blizzard`` checkout's built hub binary; CI's sibling-less job deselects it.
pytestmark = pytest.mark.needs_blizzard

#: The same sibling-resolution convention as ``test_wire_parity.py``'s ``_BLIZZARD``.
_BLIZZARD = Path(os.environ.get("BLIZZARD_SOURCE") or Path(__file__).resolve().parents[2] / "blizzard")
_HUB_BIN = _BLIZZARD / ".venv" / "bin" / "blizzard-hub"


def _real_hub_store(tmp_path: Path) -> str:
    """The URL of a store a real ``blizzard-hub init`` scaffolded under ``tmp_path``."""
    assert _HUB_BIN.is_file(), (
        f"no real blizzard-hub binary at {_HUB_BIN} — this guard proves the reset against a REAL hub store, "
        "not a skip; `uv sync` the sibling blizzard worktree, or set $BLIZZARD_SOURCE to it"
    )
    hub_dir = tmp_path / "hub"
    # No ambient BZ_* (a feature env's BZ_HUB_DB_URL would send init at a live store).
    env = {key: value for key, value in os.environ.items() if not key.startswith("BZ_")}
    init = subprocess.run([str(_HUB_BIN), "init", str(hub_dir)], env=env, capture_output=True, text=True, timeout=120)
    assert init.returncode == 0, init.stderr
    return f"sqlite:///{hub_dir / 'data' / 'hub.db'}"


def _rows_by_table(url: str) -> dict[str, Counter[str]]:
    """Every table's rows, each rendered whole."""
    engine = create_engine(url)
    meta = MetaData()
    meta.reflect(bind=engine)
    with engine.connect() as conn:
        return {
            table.name: Counter(repr(tuple(row)) for row in conn.execute(select(table)).all())
            for table in meta.sorted_tables
        }


def test_reset_keeps_every_row_a_fresh_hub_init_wrote(tmp_path: Path) -> None:
    url = _real_hub_store(tmp_path)
    before = _rows_by_table(url)
    assert before["tenants"], "a fresh init seeds a tenant — an empty snapshot would prove nothing"

    result = CliRunner().invoke(cli, ["reset", "--store", "hub", "--url", url])
    assert result.exit_code == 0, result.output

    after = _rows_by_table(url)
    lost = {table: sum((rows - after[table]).values()) for table, rows in before.items() if rows - after[table]}
    assert not lost, (
        f"the reset dropped rows a fresh `blizzard-hub init` wrote, by table: {lost} — "
        "keep them in `domain/seeding.py`'s `hub_reset_boundary`"
    )
    assert after == before

    seeded = CliRunner().invoke(cli, ["scenario", "board", "--url", url, "--seed", "1"])
    assert seeded.exit_code == 0, seeded.output
