"""Where the fake service keeps what a real one would: its registration under the state home, its store under the
data home, both resolved from the invoking process's environment exactly as OpenCode 2 resolves them."""

from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path


def _home(env: Mapping[str, str], variable: str, fallback: tuple[str, ...]) -> Path:
    value = env.get(variable)
    if value:
        return Path(value)
    return Path(env.get("HOME") or os.path.expanduser("~")).joinpath(*fallback)


def registration_path(env: Mapping[str, str]) -> Path:
    return _home(env, "XDG_STATE_HOME", (".local", "state")) / "opencode" / "service.json"


def service_log_path(env: Mapping[str, str]) -> Path:
    return _home(env, "XDG_STATE_HOME", (".local", "state")) / "opencode" / "mock-service.log"


def store_path(env: Mapping[str, str]) -> Path:
    return _home(env, "XDG_DATA_HOME", (".local", "share")) / "opencode" / "mock-opencode-2.json"


def database_path(env: Mapping[str, str]) -> Path:
    return _home(env, "XDG_DATA_HOME", (".local", "share")) / "opencode" / "opencode.db"


__all__ = ["database_path", "registration_path", "service_log_path", "store_path"]
