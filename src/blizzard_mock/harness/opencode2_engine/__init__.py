"""OpenCode 2's engine-backed mock service, emitted as a standalone, stdlib-only zipapp: the shared managed service,
with each turn's prompt run as a script in a child process of its own, in the session's directory and environment,
talking to the service over a pipe. ``harness/README.md``'s "OpenCode 2 engine-backed mode" section owns the
contract."""

from __future__ import annotations
