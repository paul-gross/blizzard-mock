"""Every mock daemon serves on ``--port 0`` and publishes its bound URL to ``--listening-file``.

Each daemon runs as its real CLI out of process; the test waits for the listening file, then
asserts the URL it names answers the health route and that self-referencing URLs carry the
bound port, never ``:0``.
"""

from __future__ import annotations

import contextlib
import subprocess
import sys
import time
from collections.abc import Iterator
from pathlib import Path
from urllib.parse import urlparse

import httpx2 as httpx

from tests.forge_world import REPO, build_repos


@contextlib.contextmanager
def _daemon(module: str, tmp_path: Path, name: str, *args: str) -> Iterator[str]:
    """Spawn ``module`` on ``--port 0`` and yield the URL its listening file publishes."""
    listening = tmp_path / f"{name}.url"
    log_path = tmp_path / f"{name}.log"
    with log_path.open("w") as log:
        proc = subprocess.Popen(
            [
                sys.executable,
                "-m",
                module,
                "--host",
                "127.0.0.1",
                "--port",
                "0",
                "--listening-file",
                str(listening),
                *args,
            ],
            stdout=log,
            stderr=subprocess.STDOUT,
        )
        try:
            deadline = time.monotonic() + 30.0
            while not listening.exists():
                assert proc.poll() is None, f"{name} exited early ({proc.returncode}):\n{log_path.read_text()}"
                assert time.monotonic() < deadline, f"{name} never published:\n{log_path.read_text()}"
                time.sleep(0.05)
            yield listening.read_text().strip()
        finally:
            proc.terminate()
            with contextlib.suppress(subprocess.TimeoutExpired):
                proc.wait(timeout=10)
            if proc.poll() is None:
                proc.kill()
                proc.wait()


def _bound_port(url: str) -> int:
    parsed = urlparse(url)
    assert parsed.scheme == "http"
    assert parsed.hostname == "127.0.0.1"
    assert parsed.port not in (None, 0)
    return parsed.port


def test_forge_publishes_its_bound_url_and_emits_it(tmp_path: Path) -> None:
    repos = build_repos(tmp_path / "repos")
    with _daemon("blizzard_mock.forge.cli", tmp_path, "forge", "--repos-dir", str(repos)) as url:
        port = _bound_port(url)
        assert httpx.get(f"{url}/healthz").status_code == 200
        body = httpx.get(f"{url}/repos/{REPO}").json()
        for key in ("url", "html_url"):
            assert urlparse(body[key]).port == port, body[key]
            assert ":0/" not in body[key]


def test_idp_publishes_its_bound_url_and_its_discovery_carries_it(tmp_path: Path) -> None:
    with _daemon("blizzard_mock.idp.cli", tmp_path, "idp") as url:
        port = _bound_port(url)
        assert httpx.get(f"{url}/healthz").status_code == 200
        discovery = httpx.get(f"{url}/.well-known/openid-configuration").json()
        assert discovery["issuer"] == url
        for key, value in discovery.items():
            if isinstance(value, str) and value.startswith("http"):
                assert urlparse(value).port == port, (key, value)


def test_mock_hub_and_mock_runner_publish_their_bound_urls(tmp_path: Path) -> None:
    with _daemon("blizzard_mock.mock_hub.cli", tmp_path, "mock-hub") as hub_url:
        _bound_port(hub_url)
        assert httpx.get(f"{hub_url}/api/health").status_code == 200
        with _daemon("blizzard_mock.mock_runner.cli", tmp_path, "mock-runner", "--hub-url", hub_url) as runner_url:
            _bound_port(runner_url)
            assert runner_url != hub_url
            assert httpx.get(f"{runner_url}/api/health").status_code == 200
