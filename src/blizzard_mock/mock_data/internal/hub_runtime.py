"""Resolve a hub/runner runtime directory (``--dir``) to a store ``db_url``.

Sugar for ``--url``: reads the runtime's config file — ``blizzard-hub.toml`` for
``--store hub``, ``blizzard-runner.toml`` for ``--store runner``, the same
filenames ``blizzard hub init``/``blizzard runner init`` scaffold and the
daemons themselves read (``blizzard/hub/config.py``, ``blizzard/runner/config.py``)
— via stdlib ``tomllib`` and pulls its ``db_url`` key. No ``blizzard`` import
(the mock-data contract's first property): the toml shape is read independently,
not via the daemon's own config loader. This also works for a postgres
deployment, since it just reads back whatever ``db_url`` the runtime's config
says. A config with no ``db_url`` key falls back to the daemons' own default —
``sqlite:///<dir>/data/<store>.db`` (issue #234). A missing config file still
fails loud, naming which file is missing.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

_CONFIG_FILENAMES = {"hub": "blizzard-hub.toml", "runner": "blizzard-runner.toml"}
_STORE_DB_FILENAMES = {"hub": "hub.db", "runner": "runner.db"}


class HubRuntimeError(Exception):
    """The runtime directory named by ``--dir`` could not be resolved to a ``db_url``."""


def resolve_db_url(runtime_dir: Path, *, store: str) -> str:
    """Read ``<runtime_dir>/<store's config file>`` and return its ``db_url``.

    A config with no ``db_url`` key resolves to the daemons' default,
    ``sqlite:///<runtime_dir>/data/<store>.db`` — mirroring
    ``HubConfig.load``/``RunnerConfig.load``, which scaffold no ``db_url`` line
    when it would just restate that default.
    """
    filename = _CONFIG_FILENAMES[store]
    path = runtime_dir / filename
    if not path.is_file():
        raise HubRuntimeError(
            f"{path} does not exist — {runtime_dir} is not an initialized {store} runtime "
            f"(run `blizzard {store} init {runtime_dir}`, or pass --url/$DATABASE_URL directly)"
        )
    raw = tomllib.loads(path.read_text())
    db_url = raw.get("db_url")
    if not db_url:
        return f"sqlite:///{(runtime_dir / 'data' / _STORE_DB_FILENAMES[store]).resolve()}"
    return str(db_url)
