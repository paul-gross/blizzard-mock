"""The fixed configuration one emitted artifact was baked with.

This copy — the one living in blizzard-mock's own source tree — holds
``emit``'s own defaults (no levers armed, the pinned version, no evidence
paths), so the package stays importable, lintable, and type-checkable during
development. ``emit.py`` never imports this file; instead, at emit time, it
writes a fresh copy of this exact module — with these four constants replaced
by the caller's validated, fixed lever set and path options — into the
staged copy of the package before zipping it, so the emitted artifact carries
its own immutable configuration.
"""

from __future__ import annotations

from .levers import Lever

LEVERS: frozenset[Lever] = frozenset()
VERSION: str = "1.18.25"
AUTH_READ_MARKER: str | None = None
VERSION_TOUCH_PATH: str | None = None
