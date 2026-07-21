"""Stub-IdP configuration — bind address (mirrors ``blizzard_mock.forge.config``)."""

from __future__ import annotations

import os
from dataclasses import dataclass

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8090

ENV_HOST = "BZ_IDP_HOST"
ENV_PORT = "BZ_IDP_PORT"


@dataclass(frozen=True)
class IdpConfig:
    """Resolved runtime configuration for the stub IdP."""

    host: str = DEFAULT_HOST
    port: int = DEFAULT_PORT

    @property
    def base_url(self) -> str:
        return f"http://{self.host}:{self.port}"

    @classmethod
    def from_env(cls, *, host: str | None = None, port: int | None = None) -> IdpConfig:
        resolved_port = port if port is not None else int(os.environ.get(ENV_PORT, DEFAULT_PORT))
        resolved_host = host or os.environ.get(ENV_HOST, DEFAULT_HOST)
        return cls(host=resolved_host, port=resolved_port)
