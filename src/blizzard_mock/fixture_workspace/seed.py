"""The toy project repos a fixture workspace mints.

Small, boring, and framework-agnostic on purpose: the fixture exists to exercise
winter's workspace binding (clone, worktree, reset, commit, push), not to run
any real application. Two repos is enough to prove multi-repo worktreeing.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass


@dataclass(frozen=True)
class RepoSeed:
    """A toy project repo: its name, its initial committed files, and the seed message."""

    name: str
    files: Mapping[str, str]
    message: str


TOY_REPOS: tuple[RepoSeed, ...] = (
    RepoSeed(
        name="toy-api",
        files={
            "README.md": "# toy-api\n\nToy backend repo for the blizzard fixture workspace.\n",
            "src/app.py": 'def hello() -> str:\n    return "toy-api"\n',
        },
        message="chore: seed toy-api",
    ),
    RepoSeed(
        name="toy-web",
        files={
            "README.md": "# toy-web\n\nToy frontend repo for the blizzard fixture workspace.\n",
            "src/index.js": "export const hello = () => 'toy-web';\n",
        },
        message="chore: seed toy-web",
    ),
)
