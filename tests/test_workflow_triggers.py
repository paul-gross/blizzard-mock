from __future__ import annotations

from pathlib import Path

import yaml

_WORKFLOWS = Path(__file__).resolve().parent.parent / ".github" / "workflows"


def _yaml11_true_key_triggers(name: str) -> dict:
    return yaml.safe_load((_WORKFLOWS / name).read_text())[True]


def test_pull_requests_into_master_and_release_lines_run_the_gate() -> None:
    assert _yaml11_true_key_triggers("pr.yml") == {"pull_request": {"branches": ["master", "release/**"]}}


def test_a_push_runs_on_master_alone() -> None:
    assert _yaml11_true_key_triggers("push.yml") == {"push": {"branches": ["master"]}}
