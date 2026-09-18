"""The default fresh turn's real-git compatibility proof.

A worktree that can accept a genuine ``git commit`` from this fake binary is
the observable a fresh (no ``--session``) turn leaves behind.
"""

from __future__ import annotations

import subprocess


def write_and_commit() -> None:
    """Write ``compatibility-proof.txt`` in the process cwd and commit it for real."""
    with open("compatibility-proof.txt", "w") as fh:
        fh.write("ok\n")
    subprocess.run(["git", "add", "compatibility-proof.txt"], check=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.email=fake@blizzard.local",
            "-c",
            "user.name=fake",
            "commit",
            "-q",
            "-m",
            "compatibility: fake proof",
        ],
        check=True,
    )
