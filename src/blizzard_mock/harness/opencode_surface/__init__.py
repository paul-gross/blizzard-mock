"""A standalone, stdlib-only fake OpenCode CLI, emitted as a zipapp.

Wholly separate from :mod:`blizzard_mock.harness.engine`'s exec mode: answers
OpenCode's real CLI/HTTP surface as a genuine subprocess, under a bare system
``python3`` with no venv import reachable. See ``harness/README.md``'s
"OpenCode CLI-surface mode" section for the full contract."""

from __future__ import annotations
