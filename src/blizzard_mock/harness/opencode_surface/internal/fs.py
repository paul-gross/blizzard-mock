"""Filesystem side effects with no decision logic of their own."""

from __future__ import annotations

import os


def touch_in_cwd(name: str) -> None:
    """Write the ``--version-touch-path`` evidence file, rooted at the process cwd."""
    with open(os.path.join(os.getcwd(), name), "w") as fh:
        fh.write("touched")
