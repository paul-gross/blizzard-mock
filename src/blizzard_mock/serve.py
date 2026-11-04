"""The one place a mock daemon serves.

The socket is bound before the app is built, so ``--port 0`` works: the caller's
``build`` receives the bound port and derives every self-referencing URL from it.
With a listening file, ``http://<host>:<bound-port>`` is published atomically once
uvicorn's startup has completed — the file existing means the daemon answers.
"""

from __future__ import annotations

import asyncio
import os
import socket
import tempfile
from collections.abc import Callable
from pathlib import Path

import uvicorn
from fastapi import FastAPI

KEEP_ALIVE_TIMEOUT_S = 75


def serve(build: Callable[[int], FastAPI], *, host: str, port: int, listening_file: Path | None = None) -> None:
    """Bind ``host:port`` (``0`` picks a free port), build the app for the bound port, and serve it."""
    family = socket.AF_INET6 if ":" in host else socket.AF_INET
    sock = socket.socket(family, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind((host, port))
    bound_port: int = sock.getsockname()[1]
    app = build(bound_port)
    server = uvicorn.Server(uvicorn.Config(app, host=host, port=bound_port, timeout_keep_alive=KEEP_ALIVE_TIMEOUT_S))
    asyncio.run(_run(server, sock, f"http://{host}:{bound_port}", listening_file))


async def _run(server: uvicorn.Server, sock: socket.socket, url: str, listening_file: Path | None) -> None:
    """``uvicorn.Server.serve`` with the listening file published between startup and the main loop."""
    config = server.config
    if not config.loaded:
        config.load()
    server.lifespan = config.lifespan_class(config)
    with server.capture_signals():
        await server.startup(sockets=[sock])
        if not server.should_exit:
            if listening_file is not None:
                _publish(listening_file, url)
            await server.main_loop()
        if server.started:
            await server.shutdown(sockets=[sock])


def _publish(path: Path, url: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    with os.fdopen(fd, "w") as fh:
        fh.write(url)
    os.replace(tmp, path)
