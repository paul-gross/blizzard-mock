"""``--version`` — the one line every surface call's version prefix is checked against."""

from __future__ import annotations


def version_line(version: str) -> str:
    """The exact text ``--version`` prints."""
    return f"opencode {version}"
