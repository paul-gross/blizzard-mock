"""Resolve a hub/runner runtime directory (``--dir``) to a store ``db_url``.

Sugar for ``--url``: reads the runtime's config file via stdlib ``tomllib``
and pulls its ``db_url`` key, no ``blizzard`` import. A config with no
``db_url`` falls back to ``sqlite:///<dir>/data/<store>.db``.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

_CONFIG_FILENAMES = {"hub": "blizzard-hub.toml", "runner": "blizzard-runner.toml"}
_STORE_DB_FILENAMES = {"hub": "hub.db", "runner": "runner.db"}


class RuntimeConfigError(Exception):
    """The runtime directory named by ``--dir`` could not be resolved to a ``db_url``."""


def resolve_db_url(runtime_dir: Path, *, store: str, url_advice: str = "--url/$DATABASE_URL") -> str:
    """Read ``<runtime_dir>/<store's config file>`` and return its ``db_url``.

    A config with no ``db_url`` key resolves to ``sqlite:///<dir>/data/<store>.db`` —
    the daemons' own default, which their config writers omit when it matches, so this
    must stay identical to their derivation. ``url_advice`` names the flag an
    unresolvable runtime should be passed instead.
    """
    filename = _CONFIG_FILENAMES[store]
    path = runtime_dir / filename
    if not path.is_file():
        raise RuntimeConfigError(
            f"{path} does not exist — {runtime_dir} is not an initialized {store} runtime "
            f"(run `blizzard {store} init {runtime_dir}`, or pass {url_advice} directly)"
        )
    raw = tomllib.loads(path.read_text())
    db_url = raw.get("db_url")
    if not db_url:
        return f"sqlite:///{(runtime_dir / 'data' / _STORE_DB_FILENAMES[store]).resolve()}"
    return str(db_url)


def resolve_runner_id(runtime_dir: Path) -> str:
    """Read ``<runtime_dir>/blizzard-runner.toml``'s ``runner_id`` — the id
    ``scenario fleet`` pins its runner-store mirror to when given ``--runner-dir``.
    The runner's own init writes this key on every start, so a runtime this tool
    can already resolve a ``db_url`` from always carries one too."""
    path = runtime_dir / _CONFIG_FILENAMES["runner"]
    if not path.is_file():
        raise RuntimeConfigError(
            f"{path} does not exist — {runtime_dir} is not an initialized runner runtime "
            f"(run `blizzard runner init {runtime_dir}`, or pass --runner-id directly)"
        )
    raw = tomllib.loads(path.read_text())
    runner_id = raw.get("runner_id")
    if not runner_id:
        raise RuntimeConfigError(f"{path} carries no runner_id — pass --runner-id directly")
    return str(runner_id)
