from __future__ import annotations

import uvicorn
from fastapi import FastAPI

KEEP_ALIVE_TIMEOUT_S = 75


def serve(app: FastAPI, *, host: str, port: int) -> None:
    uvicorn.run(app, host=host, port=port, timeout_keep_alive=KEEP_ALIVE_TIMEOUT_S)
