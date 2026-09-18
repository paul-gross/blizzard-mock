"""Read/write the cross-invocation session-state JSON document."""

from __future__ import annotations

import json


def read_state(path: str) -> dict:
    """The persisted state, or ``{}`` when no file has been written yet."""
    try:
        with open(path) as fh:
            return json.load(fh)
    except FileNotFoundError:
        return {}


def write_state(path: str, state: dict) -> None:
    """Persist ``state``, replacing whatever was there before."""
    with open(path, "w") as fh:
        json.dump(state, fh)
