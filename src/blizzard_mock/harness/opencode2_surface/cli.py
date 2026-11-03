"""The emitted artifact's entry point: ``--version``, ``service start|stop|status|restart`` (and the internal
``service serve`` the start detaches), ``api --standalone GET /api/session``, and ``--session <id>``."""

from __future__ import annotations

import os
import sys

from . import _baked
from .internal import migration, service, tui

_USAGE = (
    "usage: opencode --version | service start|stop|status|restart | api --standalone GET /api/session | --session <id>"
)


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else list(argv)
    env = os.environ
    argv0 = sys.argv[0]
    levers, version = _baked.LEVERS, _baked.VERSION
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
            return service.serve(env, argv0, levers, version)
    if args == ["api", "--standalone", "GET", "/api/session"]:
        return migration.list_sessions(env)
    if len(args) == 2 and args[0] == "--session":
        return tui.run(env, args[1])
    print(_USAGE, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
