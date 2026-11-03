"""A standalone, stdlib-only fake OpenCode 2, emitted as a zipapp.

It shares no code with :mod:`blizzard_mock.harness.opencode_surface` (OpenCode 1's) or with the exec engine: it
reports its baked version, registers and answers as the managed service with fixed, corpus-shaped responses for the
``/api/*`` subset, and never executes a caller-supplied prompt. See ``harness/README.md``'s "OpenCode 2 CLI-surface
mode" section for the full contract."""

from __future__ import annotations
