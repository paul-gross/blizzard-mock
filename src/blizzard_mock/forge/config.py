"""Forge configuration — bind address and the bare-repo directory.

Sourced from CLI flags with env fallbacks: the winter service band injects
``BZ_FORGE_PORT`` (band +1), and the repos directory points at the fixture
workspace's bare origins.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8080

ENV_HOST = "BZ_FORGE_HOST"
ENV_PORT = "BZ_FORGE_PORT"
ENV_REPOS_DIR = "BZ_FORGE_REPOS_DIR"


@dataclass(frozen=True)
class ForgeConfig:
    """Resolved runtime configuration for the forge service."""

    repos_dir: Path
    host: str = DEFAULT_HOST
    port: int = DEFAULT_PORT

    @property
    def base_url(self) -> str:
        return f"http://{self.host}:{self.port}"

    @classmethod
    def from_env(
        cls,
        *,
        repos_dir: str | None = None,
        host: str | None = None,
        port: int | None = None,
    ) -> ForgeConfig:
        """Resolve config, CLI value taking precedence over the env var."""
        resolved_dir = repos_dir or os.environ.get(ENV_REPOS_DIR)
        if not resolved_dir:
            raise ValueError(f"repos directory is required (pass --repos-dir or set {ENV_REPOS_DIR})")
        resolved_port = port if port is not None else int(os.environ.get(ENV_PORT, DEFAULT_PORT))
        resolved_host = host or os.environ.get(ENV_HOST, DEFAULT_HOST)
        return cls(repos_dir=Path(resolved_dir).resolve(), host=resolved_host, port=resolved_port)
