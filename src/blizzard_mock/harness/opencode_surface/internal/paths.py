"""Where this process's evidence lives — XDG-rooted, so two separate invocations
(the diagnostic spawns one process per surface call) agree on a location."""

from __future__ import annotations

import os

STATE_FILENAME = "fake-opencode-state.json"


def state_path() -> str:
    """Where the cross-invocation session-state document lives."""
    return os.path.join(os.environ.get("XDG_STATE_HOME", "."), STATE_FILENAME)


def auth_path() -> str:
    """Where the real OpenCode CLI's auth document would live."""
    return os.path.join(os.environ.get("XDG_DATA_HOME", "."), "opencode", "auth.json")
