"""Unit coverage for ``--dir`` runtime-config resolution (``blizzard-mock:unit-test``).

``internal/runtime_config.resolve_db_url`` reads a written ``blizzard-hub.toml``/
``blizzard-runner.toml`` independently — no ``blizzard`` import, no store connection.
"""

from __future__ import annotations

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


def test_resolves_the_runner_id_from_a_written_runner_toml(tmp_path: Path) -> None:
    (tmp_path / "blizzard-runner.toml").write_text('db_url = "sqlite:///runner.db"\nrunner_id = "runner-local"\n')
    assert resolve_runner_id(tmp_path) == "runner-local"


def test_missing_runner_toml_fails_naming_the_file(tmp_path: Path) -> None:
    with pytest.raises(RuntimeConfigError) as excinfo:
        resolve_runner_id(tmp_path)
    message = str(excinfo.value)
    assert "blizzard-runner.toml" in message
    assert str(tmp_path) in message


def test_runner_toml_missing_runner_id_fails_naming_the_key(tmp_path: Path) -> None:
    (tmp_path / "blizzard-runner.toml").write_text('db_url = "sqlite:///runner.db"\n')
    with pytest.raises(RuntimeConfigError, match="runner_id"):
        resolve_runner_id(tmp_path)
