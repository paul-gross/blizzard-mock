"""Resolve the effective configuration document the "...configuration turn" probes.

Mirrors real OpenCode's precedence: ``OPENCODE_CONFIG``, then the project
``opencode.json`` (unless disabled), then the XDG config file, then — as a
final override — ``OPENCODE_CONFIG_CONTENT`` verbatim.
"""

from __future__ import annotations

import json
import os
from collections.abc import Mapping


def _read_config(path: str | None) -> dict | None:
    if not path:
        return None
    try:
        with open(path) as fh:
            return json.load(fh)
    except (OSError, json.JSONDecodeError):
        return None


def resolve_config(env: Mapping[str, str], cwd: str) -> dict:
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
        config = json.loads(content)
    return config
