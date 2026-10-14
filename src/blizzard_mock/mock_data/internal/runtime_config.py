"""Resolve a hub or host runtime directory (``--dir``) to a store ``db_url``, and a host
directory to the id its hub minted its runner.

Sugar for ``--url``, with no ``blizzard`` import; a config with no ``db_url`` falls back to
``sqlite:///<dir>/data/<store>.db``. A host directory's runner id is its ``[[runner]] id``.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

from sqlalchemy import Engine, MetaData, Table, select

#: The runner store's identity table — the id and name of its latest registration.
_IDENTITY_TABLE = "runner_identity"

_CONFIG_FILENAMES = {"hub": "blizzard-hub.toml", "runner": "blizzard-host.toml"}
_STORE_DB_FILENAMES = {"hub": "hub.db", "runner": "host.db"}
_INIT_VERBS = {"hub": "blizzard hub init", "runner": "blizzard runner init"}


class RuntimeConfigError(Exception):
    """The runtime directory named by ``--dir`` could not be resolved to a ``db_url`` or runner id."""


def _read_config(runtime_dir: Path, store: str, url_advice: str) -> dict[str, object]:
    path = runtime_dir / _CONFIG_FILENAMES[store]
    if not path.is_file():
        raise RuntimeConfigError(
            f"{path} does not exist — {runtime_dir} is not an initialized {store} runtime "
            f"(run `{_INIT_VERBS[store]} {runtime_dir}`, or pass {url_advice} directly)"
        )
    try:
        return tomllib.loads(path.read_text())
    except tomllib.TOMLDecodeError as exc:
        raise RuntimeConfigError(f"{path} is not valid TOML: {exc}") from exc


def resolve_db_url(runtime_dir: Path, *, store: str, url_advice: str = "--url/$DATABASE_URL") -> str:
    """Read ``<runtime_dir>``'s config file — ``blizzard-hub.toml`` for the hub store,
    ``blizzard-host.toml`` for the runner store — and return its ``db_url``.

    A config with no ``db_url`` key resolves to ``sqlite:///<dir>/data/<store>.db`` —
    the daemons' own default, which their config writers omit when it matches, so this
    must stay identical to their derivation. ``url_advice`` names the flag an
    unresolvable runtime should be passed instead.

    A host's default ``data/host.db`` that does not exist is refused, not opened: opening it would create an
    empty file ``blizzard runner migrate`` then refuses to adopt over.
    """
    db_url = _read_config(runtime_dir, store, url_advice).get("db_url")
    if db_url:
        return str(db_url)
    store_path = (runtime_dir / "data" / _STORE_DB_FILENAMES[store]).resolve()
    if store == "runner" and not store_path.exists():
        raise RuntimeConfigError(
            f"{store_path} does not exist — run `blizzard runner migrate --dir {runtime_dir}` to create it "
            f"(or pass {url_advice} directly)"
        )
    return f"sqlite:///{store_path}"


def resolve_runner_id(runtime_dir: Path) -> str:
    """The hub-minted id of ``<runtime_dir>``'s one runner — its ``blizzard-host.toml``
    ``[[runner]] id``, which ``init`` writes once the runner joins its hub. The id
    ``--runner-dir`` pins a runner store's rows to; a host of several runners names none."""
    raw = _read_config(runtime_dir, "runner", "--runner-id")
    path = runtime_dir / _CONFIG_FILENAMES["runner"]
    runners = raw.get("runner")
    if not isinstance(runners, list) or not runners:
        raise RuntimeConfigError(f"{path} declares no [[runner]] — pass --runner-id directly")
    if len(runners) > 1:
        raise RuntimeConfigError(
            f"{path} declares {len(runners)} [[runner]] entries — pass --runner-id to name the one to pin"
        )
    entry = runners[0]
    runner_id = entry.get("id") if isinstance(entry, dict) else None
    if not runner_id:
        raise RuntimeConfigError(
            f"{path}'s [[runner]] has no id — it has not joined its hub yet "
            f"(run `blizzard runner init {runtime_dir}` against its hub, or pass --runner-id directly)"
        )
    return str(runner_id)


def read_identity_runner_id(engine: Engine) -> str | None:
    """The id of a runner store's latest registration, from its identity row; ``None`` when
    the runner has never registered. Raises ``NoSuchTableError`` against a store with no
    identity table."""
    identity = Table(_IDENTITY_TABLE, MetaData(), autoload_with=engine)
    with engine.connect() as conn:
        row = conn.execute(select(identity.c.runner_id).order_by(identity.c.id.desc()).limit(1)).first()
    return None if row is None else str(row.runner_id)
