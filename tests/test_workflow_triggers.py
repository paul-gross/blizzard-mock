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


def test_both_events_call_the_same_gate() -> None:
    for name in ("pr.yml", "push.yml"):
        workflow = yaml.safe_load((_WORKFLOWS / name).read_text())
        assert workflow["jobs"]["gate"]["uses"] == "./.github/workflows/gate.yml"


def test_parity_is_a_separate_named_job_with_sibling_checkout() -> None:
    gate = yaml.safe_load((_WORKFLOWS / "gate.yml").read_text())
    assert set(gate["jobs"]) == {"quality", "test", "process-ref-lint", "parity"}
    assert gate["jobs"]["parity"]["name"] == "wire parity (needs_blizzard)"
    steps = gate["jobs"]["parity"]["steps"]
    assert any(step.get("uses") == "./blizzard-mock/.github/actions/resolve-blizzard-ref" for step in steps)
    test_step = next(step for step in steps if step.get("run") == "mise run test -- -m needs_blizzard")
    assert test_step["env"]["BLIZZARD_SOURCE"] == "${{ github.workspace }}/blizzard"
    assert test_step["env"]["BLIZZARD_RUNNER_BIN"] == "${{ github.workspace }}/blizzard/.venv/bin/blizzard-runner"


def test_nightly_calls_gate_with_explicit_master_ref() -> None:
    nightly = yaml.safe_load((_WORKFLOWS / "nightly.yml").read_text())
    assert nightly[True]["schedule"]
    assert nightly["jobs"]["gate"] == {
        "uses": "./.github/workflows/gate.yml",
        "with": {"blizzard-ref": "master"},
    }
    gate = yaml.safe_load((_WORKFLOWS / "gate.yml").read_text())
    assert gate[True]["workflow_call"]["inputs"]["blizzard-ref"]["default"] == ""
