"""The fixed configuration one emitted artifact was baked with.

This in-tree copy holds ``emit``'s own defaults, so the package stays
importable and type-checkable during development — ``emit.py`` never imports
it, instead writing a fresh copy with these four constants replaced by the
caller's validated set into the staged package before zipping it."""

from __future__ import annotations

from .levers import Lever

LEVERS: frozenset[Lever] = frozenset()
VERSION: str = "1.18.25"
AUTH_READ_MARKER: str | None = None
VERSION_TOUCH_PATH: str | None = None
