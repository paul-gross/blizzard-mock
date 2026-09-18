"""``SECURITY_COMMAND_EXECUTES``'s actual shell execution — the one place this
package ever runs caller-shaped text as a command."""

from __future__ import annotations

import subprocess


def execute(command: str) -> None:
    """Run ``command`` for real — only ever called when the lever is armed."""
    subprocess.run(command, shell=True, check=False)
