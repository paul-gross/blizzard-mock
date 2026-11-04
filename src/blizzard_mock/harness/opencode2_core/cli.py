"""The verbs every OpenCode 2 artifact answers: ``--version``, ``service start|stop|status|restart`` (and the internal
``service serve`` the start detaches), ``api --standalone GET /api/session``, and ``--session <id>``."""

from __future__ import annotations

from collections.abc import Mapping

from .internal import migration, service, tui
from .internal.sessions import TurnRunner
from .levers import Lever

USAGE = "opencode --version | service start|stop|status|restart | api --standalone GET /api/session | --session <id>"


def dispatch(
    args: list[str],
    *,
    env: Mapping[str, str],
    argv0: str,
    levers: frozenset[Lever],
    version: str,
    runner: TurnRunner,
) -> int | None:
    """Answer ``args`` when it is a shared verb; ``None`` leaves it to the artifact's own entry point."""
    if args == ["--version"]:
        print(f"opencode v{version}")
        return 0
    if len(args) == 2 and args[0] == "service":
        verb = args[1]
        if verb == "start":
            return service.start(env, argv0, levers, version)
        if verb == "stop":
            return service.stop(env)
        if verb == "status":
            return service.status(env)
        if verb == "restart":
            return service.restart(env, argv0, levers, version)
        if verb == "serve":
            return service.serve(env, argv0, levers, version, runner)
    if args == ["api", "--standalone", "GET", "/api/session"]:
        return migration.list_sessions(env)
    if len(args) == 2 and args[0] == "--session":
        return tui.run(env, args[1])
    return None


__all__ = ["USAGE", "dispatch"]
