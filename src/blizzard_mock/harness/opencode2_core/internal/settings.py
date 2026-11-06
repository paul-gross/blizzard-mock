"""``service get|set|unset disabled``: the one service setting the fake keeps, in the service configuration file beside
the operator's other OpenCode configuration, as OpenCode 2 keeps it."""

from __future__ import annotations

import json
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import cast

from . import paths

DISABLED = "disabled"


def _read(path: Path) -> dict[str, object]:
    try:
        loaded = json.loads(path.read_text())
    except (OSError, ValueError):
        return {}
    return cast("dict[str, object]", loaded) if isinstance(loaded, dict) else {}


def _write(path: Path, config: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(config))
    path.chmod(0o600)


def get(env: Mapping[str, str], key: str) -> int:
    if key != DISABLED:
        print(f"Unknown service config key: {key}", file=sys.stderr)
        return 1
    print("true" if _read(paths.service_config_path(env)).get(DISABLED) is True else "false")
    return 0


def set_(env: Mapping[str, str], key: str, value: str) -> int:
    if key != DISABLED:
        print(f"Unknown service config key: {key}", file=sys.stderr)
        return 1
    if value not in ("true", "false"):
        print("Disabled must be true or false", file=sys.stderr)
        return 1
    path = paths.service_config_path(env)
    _write(path, {**_read(path), DISABLED: value == "true"})
    return 0


def unset(env: Mapping[str, str], key: str) -> int:
    if key != DISABLED:
        print(f"Unknown service config key: {key}", file=sys.stderr)
        return 1
    path = paths.service_config_path(env)
    config = _read(path)
    config.pop(DISABLED, None)
    _write(path, config)
    return 0


__all__ = ["DISABLED", "get", "set_", "unset"]
