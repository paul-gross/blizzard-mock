"""Unit coverage for ``--dir`` runtime-config resolution (``blizzard-mock:unit-test``).

``internal/runtime_config.resolve_db_url`` reads a written ``blizzard-hub.toml``/
``blizzard-runner.toml`` independently — no ``blizzard`` import, no store connection.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from blizzard_mock.mock_data.internal.runtime_config import RuntimeConfigError, resolve_db_url, resolve_runner_id


def test_resolves_db_url_from_a_written_hub_toml(tmp_path: Path) -> None:
    (tmp_path / "blizzard-hub.toml").write_text('db_url = "sqlite:///hub.db"\n')
    assert resolve_db_url(tmp_path, store="hub") == "sqlite:///hub.db"


def test_resolves_db_url_from_a_written_runner_toml(tmp_path: Path) -> None:
    (tmp_path / "blizzard-runner.toml").write_text('db_url = "sqlite:///runner.db"\n')
    assert resolve_db_url(tmp_path, store="runner") == "sqlite:///runner.db"


def test_resolves_a_postgres_db_url_too(tmp_path: Path) -> None:
    (tmp_path / "blizzard-hub.toml").write_text('db_url = "postgresql+psycopg://u:p@host:5432/db"\n')
    assert resolve_db_url(tmp_path, store="hub") == "postgresql+psycopg://u:p@host:5432/db"


def test_missing_config_file_fails_naming_the_file(tmp_path: Path) -> None:
    with pytest.raises(RuntimeConfigError) as excinfo:
        resolve_db_url(tmp_path, store="hub")
    message = str(excinfo.value)
    assert "blizzard-hub.toml" in message
    assert str(tmp_path) in message


def test_config_missing_db_url_falls_back_to_the_stores_default_path(tmp_path: Path) -> None:
    """Blizzard's issue-#234 scaffold omits ``db_url`` when it is the default; the
    resolver derives the same ``sqlite:///<dir>/data/<store>.db`` the daemons do."""
    (tmp_path / "blizzard-hub.toml").write_text('host = "127.0.0.1"\n')
    assert resolve_db_url(tmp_path, store="hub") == f"sqlite:///{(tmp_path / 'data' / 'hub.db').resolve()}"


def test_config_missing_db_url_falls_back_to_the_runner_default_too(tmp_path: Path) -> None:
    (tmp_path / "blizzard-runner.toml").write_text('host = "127.0.0.1"\n')
    assert resolve_db_url(tmp_path, store="runner") == f"sqlite:///{(tmp_path / 'data' / 'runner.db').resolve()}"


def _runner_store(tmp_path: Path, *rows: tuple[str, str]) -> None:
    """A runner runtime whose store carries the identity table (hand-made, as the runner's
    migration shapes it) holding ``rows`` of ``(runner_id, runner_name)``, oldest first."""
    db = tmp_path / "runner.db"
    (tmp_path / "blizzard-runner.toml").write_text(f'db_url = "sqlite:///{db}"\n')
    with sqlite3.connect(db) as conn:
        conn.execute(
            "CREATE TABLE runner_identity (id INTEGER PRIMARY KEY AUTOINCREMENT, runner_id TEXT NOT NULL, "
            "runner_name TEXT NOT NULL, registered_at TEXT NOT NULL)"
        )
        conn.executemany(
            "INSERT INTO runner_identity (runner_id, runner_name, registered_at) VALUES (?, ?, '2026-07-13')", rows
        )


def test_resolves_the_runner_id_from_the_runner_store_identity_row(tmp_path: Path) -> None:
    _runner_store(tmp_path, ("rn_01OLDER0000000000000000000", "runner-local"), ("rn_01NEWER0000000000000000000", "r"))
    assert resolve_runner_id(tmp_path) == "rn_01NEWER0000000000000000000"


def test_a_runner_that_never_registered_fails_saying_so(tmp_path: Path) -> None:
    _runner_store(tmp_path)
    with pytest.raises(RuntimeConfigError, match="never registered"):
        resolve_runner_id(tmp_path)


def test_an_unmigrated_runner_store_fails_naming_the_identity_table(tmp_path: Path) -> None:
    (tmp_path / "blizzard-runner.toml").write_text(f'db_url = "sqlite:///{tmp_path / "runner.db"}"\n')
    with pytest.raises(RuntimeConfigError, match="runner_identity"):
        resolve_runner_id(tmp_path)


def test_missing_runner_toml_fails_naming_the_file(tmp_path: Path) -> None:
    with pytest.raises(RuntimeConfigError) as excinfo:
        resolve_runner_id(tmp_path)
    message = str(excinfo.value)
    assert "blizzard-runner.toml" in message
    assert str(tmp_path) in message
