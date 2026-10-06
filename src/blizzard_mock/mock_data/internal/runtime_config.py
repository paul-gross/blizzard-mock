"""Resolve a hub/runner runtime directory (``--dir``) to a store ``db_url``, and a runner
runtime to the id its hub minted.

Sugar for ``--url``, with no ``blizzard`` import; a config with no ``db_url`` falls back to
``sqlite:///<dir>/data/<store>.db``. The runner's id is the runner store's identity row, read by reflection.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

from sqlalchemy import MetaData, Table, create_engine, select
from sqlalchemy.exc import NoSuchTableError, SQLAlchemyError

#: The runner store's singleton identity table — the id and name of its latest registration.
_IDENTITY_TABLE = "runner_identity"

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
    """Read the runner id its hub minted from ``<runtime_dir>``'s runner store — the identity
    row the runner records on every successful registration — the id ``scenario fleet`` pins
    its runner-store mirror to when given ``--runner-dir``. A runner that has never registered
    holds none yet."""
    db_url = resolve_db_url(runtime_dir, store="runner")
    engine = create_engine(db_url)
    try:
        identity = Table(_IDENTITY_TABLE, MetaData(), autoload_with=engine)
        with engine.connect() as conn:
            row = conn.execute(select(identity.c.runner_id).order_by(identity.c.id.desc()).limit(1)).first()
    except NoSuchTableError as exc:
        raise RuntimeConfigError(
            f"the runner store at {db_url} has no {_IDENTITY_TABLE} table — is it migrated? "
            "(start the runner once, or pass --runner-id directly)"
        ) from exc
    except SQLAlchemyError as exc:
        raise RuntimeConfigError(f"could not read the runner store at {db_url}: {exc}") from exc
    finally:
        engine.dispose()
    if row is None:
        raise RuntimeConfigError(
            f"the runner at {runtime_dir} has never registered with its hub, so it holds no id yet "
            "— start it once against its hub, or pass --runner-id directly"
        )
    return str(row.runner_id)
