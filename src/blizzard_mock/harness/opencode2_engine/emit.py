"""Bake a validated lever set into a standalone zipapp engine-backed fake-OpenCode-2 binary.

Runs inside blizzard-mock's own venv at ``mock-opencode-2 emit-engine`` time, never inside the emitted artifact."""

from __future__ import annotations

from pathlib import Path

from ..opencode2_core.emit import DEFAULT_VERSION, bake
from ..opencode2_core.levers import Lever

_PACKAGE_ROOT = Path(__file__).resolve().parent


def emit(out: Path, *, levers: frozenset[Lever] = frozenset(), version: str = DEFAULT_VERSION) -> None:
    """Write an executable zipapp to ``out``, baked with the given lever set. ``out``'s parent must exist."""
    bake(out, _PACKAGE_ROOT, levers=levers, version=version)


__all__ = ["DEFAULT_VERSION", "emit"]
