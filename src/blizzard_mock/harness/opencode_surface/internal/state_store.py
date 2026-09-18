"""Read/write the cross-invocation session-state JSON document."""

from __future__ import annotations

import json
import os
import tempfile


def read_state(path: str) -> dict:
    """The persisted state, or ``{}`` when no file has been written yet."""
    try:
        with open(path) as fh:
            return json.load(fh)
    except FileNotFoundError:
        return {}


def write_state(path: str, state: dict) -> None:
    """Persist ``state``, atomically: a temp file in the same directory, renamed over the
    target — the diagnostic's 0.1s poll only guards a missing file, never a partial one."""
    directory = os.path.dirname(path) or "."
    fd, tmp_path = tempfile.mkstemp(dir=directory, prefix=os.path.basename(path) + ".")
    with os.fdopen(fd, "w") as fh:
        json.dump(state, fh)
    os.replace(tmp_path, path)
