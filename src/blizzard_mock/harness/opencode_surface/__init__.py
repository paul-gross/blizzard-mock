"""A standalone, stdlib-only fake OpenCode CLI, emitted as a zipapp.

Wholly separate from :mod:`blizzard_mock.harness.engine`'s "prompt is the
program" exec mode: this package answers OpenCode's real CLI/HTTP surface
(``--version``, ``run``, ``export``, ``serve``, ``attach``,
``debug config --pure``) so a real diagnostic can spawn the emitted artifact
as a genuine out-of-process subprocess, under a bare system ``python3`` with
no venv and no ``blizzard_mock`` import reachable at runtime.

``domain/`` holds pure decision logic (no file I/O, no sockets, no argparse);
``internal/`` holds the I/O adapters (state-file, HTTP server, HTTP client,
subprocess); ``cli.py`` composes the two into the artifact's entry point;
``levers.py`` is the closed, emit-time-only misbehaviour vocabulary;
``emit.py`` — not part of the emitted artifact — bakes a validated lever set
into a zipapp from blizzard-mock's own venv.
"""

from __future__ import annotations
