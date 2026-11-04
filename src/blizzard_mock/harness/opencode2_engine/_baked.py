"""The fixed configuration one emitted engine-backed artifact was baked with.

This in-tree copy holds ``emit``'s own defaults, so the package stays importable and type-checkable during
development — ``emit.py`` never imports it, instead writing a fresh copy with these constants replaced by the
caller's validated set into the staged package before zipping it."""

from __future__ import annotations

from ..opencode2_core.levers import Lever

LEVERS: frozenset[Lever] = frozenset()
VERSION: str = "2.1.4"
