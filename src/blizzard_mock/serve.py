"""The one uvicorn entrypoint every mock daemon serves through.

Shared by each mock's ``cli`` so the server settings live in a single place.
"""

from __future__ import annotations

import uvicorn
from fastapi import FastAPI

#: Seconds an idle connection stays open. Must outlast the idle gaps between a test client's
#: requests: a pooled-connection reuse racing uvicorn's close drops the request.
KEEP_ALIVE_TIMEOUT_S = 75


def serve(app: FastAPI, *, host: str, port: int) -> None:
    """Run ``app`` under uvicorn on ``host``:``port`` until interrupted."""
    uvicorn.run(app, host=host, port=port, timeout_keep_alive=KEEP_ALIVE_TIMEOUT_S)
