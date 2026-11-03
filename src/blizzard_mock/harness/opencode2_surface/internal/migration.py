"""``api --standalone GET /api/session``: opening an OpenCode 1 store the way 2.x does the first time — in place, the
1.x tables untouched, the 2.x tables added, and the migration marked in ``kv`` where OpenCode 1's health reads it."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Mapping
from contextlib import closing

from . import paths

MIGRATION_KV_KEY = "migration.v1-v2"


def list_sessions(env: Mapping[str, str]) -> int:
    database = paths.database_path(env)
    sessions: list[dict[str, object]] = []
    if database.is_file():
        with closing(sqlite3.connect(database)) as db, db:
            db.execute("create table if not exists kv (key text primary key, value text)")
            db.execute("create table if not exists session_v2 (id text primary key, data text)")
            tables = {name for (name,) in db.execute("select name from sqlite_master where type = 'table'")}
            if "session" in tables:
                for (session_id,) in db.execute("select id from session order by 1"):
                    db.execute("insert or ignore into session_v2 values (?, ?)", (session_id, "{}"))
                    sessions.append({"id": session_id})
            db.execute("insert or replace into kv values (?, ?)", (MIGRATION_KV_KEY, json.dumps({"phase": "sessions"})))
    print(json.dumps({"data": sessions}))
    return 0


__all__ = ["MIGRATION_KV_KEY", "list_sessions"]
