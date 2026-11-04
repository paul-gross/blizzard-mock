"""Resolve the effective configuration document the "...configuration turn" probes.

Mirrors real OpenCode's precedence: ``OPENCODE_CONFIG``, then the project
``opencode.json`` (unless disabled), then the XDG config file, then — as a
final override — ``OPENCODE_CONFIG_CONTENT`` verbatim.
"""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from typing import cast


def _as_object(document: object) -> dict[str, object] | None:
    """``document`` when it is a JSON object, else ``None``."""
    return cast("dict[str, object]", document) if isinstance(document, dict) else None


def _read_config(path: str | None) -> dict[str, object] | None:
    if not path:
        return None
    try:
        with open(path) as fh:
            return _as_object(json.load(fh))
    except (OSError, json.JSONDecodeError):
        return None


def resolve_config(env: Mapping[str, str], cwd: str) -> dict[str, object]:
    """The effective config dict, following real OpenCode's file precedence."""
    config = _read_config(env.get("OPENCODE_CONFIG", ""))
    if config is None and env.get("OPENCODE_DISABLE_PROJECT_CONFIG") != "1":
        config = _read_config(os.path.join(cwd, "opencode.json"))
    if config is None:
        config = _read_config(os.path.join(env.get("XDG_CONFIG_HOME", ""), "opencode", "opencode.json"))
    if config is None:
        config = {}
    content = env.get("OPENCODE_CONFIG_CONTENT")
    if content:
        config = _as_object(json.loads(content)) or {}
    return config
