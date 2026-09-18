"""``READ_AUTH``/``MUTATE_AUTH``'s access to the real OpenCode CLI's auth document."""

from __future__ import annotations

from . import paths


def read_status() -> str:
    """``present``/``missing``/``unreadable`` — one byte is read to distinguish the last two."""
    try:
        with open(paths.auth_path(), "rb") as fh:
            fh.read(1)
    except FileNotFoundError:
        return "missing"
    except OSError:
        return "unreadable"
    return "present"


def write_marker(marker_path: str, status: str) -> None:
    """Record ``status`` to the evidence-capture path ``--auth-read-marker`` named."""
    try:
        with open(marker_path, "w") as fh:
            fh.write(status)
    except OSError:
        pass


def mutate() -> None:
    """Overwrite the auth document with fake content — ``MUTATE_AUTH``'s whole point."""
    try:
        with open(paths.auth_path(), "w") as fh:
            fh.write("mutated by fake")
    except OSError:
        pass
