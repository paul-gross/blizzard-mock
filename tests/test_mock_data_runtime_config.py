"""Unit coverage for ``--dir`` runtime-config resolution (``blizzard-mock:unit-test``).

``internal/runtime_config`` reads a written ``blizzard-hub.toml``/``blizzard-host.toml``
independently — no ``blizzard`` import, no store connection.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from blizzard_mock.mock_data.internal.runtime_config import RuntimeConfigError, resolve_db_url, resolve_runner_id


def test_resolves_db_url_from_a_written_hub_toml(tmp_path: Path) -> None:
    (tmp_path / "blizzard-hub.toml").write_text('db_url = "sqlite:///hub.db"\n')
    assert resolve_db_url(tmp_path, store="hub") == "sqlite:///hub.db"


def test_resolves_db_url_from_a_written_host_toml(tmp_path: Path) -> None:
    (tmp_path / "blizzard-host.toml").write_text('db_url = "sqlite:///elsewhere.db"\n')
    assert resolve_db_url(tmp_path, store="runner") == "sqlite:///elsewhere.db"


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
    """A daemon's scaffold omits ``db_url`` when it is the default; the resolver derives
    the same ``sqlite:///<dir>/data/<store>.db`` the daemons do."""
    (tmp_path / "blizzard-hub.toml").write_text('host = "127.0.0.1"\n')
    assert resolve_db_url(tmp_path, store="hub") == f"sqlite:///{(tmp_path / 'data' / 'hub.db').resolve()}"


def test_host_toml_missing_db_url_falls_back_to_the_host_store(tmp_path: Path) -> None:
    (tmp_path / "blizzard-host.toml").write_text('host = "127.0.0.1"\n')
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "host.db").touch()
    assert resolve_db_url(tmp_path, store="runner") == f"sqlite:///{(tmp_path / 'data' / 'host.db').resolve()}"


def test_a_host_without_its_default_store_is_refused_naming_migrate_and_creates_nothing(tmp_path: Path) -> None:
    (tmp_path / "blizzard-host.toml").write_text('host = "127.0.0.1"\n')

    with pytest.raises(RuntimeConfigError, match=r"blizzard runner migrate --dir"):
        resolve_db_url(tmp_path, store="runner")

    assert not (tmp_path / "data").exists()


def test_a_legacy_runner_toml_is_not_a_host_runtime(tmp_path: Path) -> None:
    (tmp_path / "blizzard-runner.toml").write_text('db_url = "sqlite:///runner.db"\n')
    with pytest.raises(RuntimeConfigError, match=r"blizzard-host\.toml does not exist"):
        resolve_db_url(tmp_path, store="runner")


_RUNNER_ID = "rn_01JA0000000000000000000000"


def _host_toml(tmp_path: Path, *runners: str) -> None:
    (tmp_path / "blizzard-host.toml").write_text('hub_url = "http://127.0.0.1:8001"\n' + "".join(runners))


def test_resolves_the_runner_id_from_the_host_files_runner(tmp_path: Path) -> None:
    """Read from the file alone — no store exists to hold an identity row."""
    _host_toml(tmp_path, f'\n[[runner]]\nid = "{_RUNNER_ID}"\nname = "runner-local"\nsubscription = "anthropic"\n')
    assert resolve_runner_id(tmp_path) == _RUNNER_ID


def test_a_runner_with_no_id_fails_saying_so(tmp_path: Path) -> None:
    _host_toml(tmp_path, '\n[[runner]]\nname = "runner-local"\n')
    with pytest.raises(RuntimeConfigError, match=r"\[\[runner\]\] has no id"):
        resolve_runner_id(tmp_path)


def test_a_host_with_no_runner_fails_saying_so(tmp_path: Path) -> None:
    _host_toml(tmp_path)
    with pytest.raises(RuntimeConfigError, match=r"declares no \[\[runner\]\]"):
        resolve_runner_id(tmp_path)


def test_a_host_of_several_runners_names_none(tmp_path: Path) -> None:
    _host_toml(
        tmp_path, f'\n[[runner]]\nid = "{_RUNNER_ID}"\nname = "a"\n', '\n[[runner]]\nid = "rn_01JB"\nname = "b"\n'
    )
    with pytest.raises(RuntimeConfigError, match=r"2 \[\[runner\]\] entries.*--runner-id"):
        resolve_runner_id(tmp_path)


def test_missing_host_toml_fails_naming_the_file(tmp_path: Path) -> None:
    with pytest.raises(RuntimeConfigError) as excinfo:
        resolve_runner_id(tmp_path)
    message = str(excinfo.value)
    assert "blizzard-host.toml" in message
    assert str(tmp_path) in message
