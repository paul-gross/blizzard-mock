"""mock-opencode's billed-zero mode: a subscription-billed OpenCode reports ``cost: 0`` on
every step and message while keeping its tokens. Off, the synthesized cost stands."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from blizzard_mock.harness.engine import RunResult
from blizzard_mock.harness.facades._opencode_transcript import (
    BILLED_ZERO_ENV_VAR,
    OpenCodeTranscriptWriter,
    billed_zero_mode,
    document_path,
)
from blizzard_mock.harness.facades.opencode import OpenCodeRunWire


def _step_finish(wire: OpenCodeRunWire) -> dict:
    lines = [json.loads(line) for line in wire.render(RunResult(session_id="ses_1", text="done here")).splitlines()]
    (finish,) = [line for line in lines if line["type"] == "step_finish"]
    return finish["part"]


def _assistant(tmp_path: Path, *, billed_zero: bool) -> dict:
    writer = OpenCodeTranscriptWriter(session_id="sess-bz", root=tmp_path, cwd=tmp_path, billed_zero=billed_zero)
    writer.record_user("go")
    writer.record_result(RunResult(session_id="sess-bz", text="done here"))
    doc = json.loads(document_path(tmp_path, "sess-bz").read_text())
    return next(m for m in doc["messages"] if m["info"]["role"] == "assistant")


def test_the_run_stream_reports_a_synthesized_cost_by_default() -> None:
    assert _step_finish(OpenCodeRunWire())["cost"] > 0


def test_the_run_stream_reports_zero_cost_and_keeps_tokens_when_billed_zero() -> None:
    billed, zero = _step_finish(OpenCodeRunWire()), _step_finish(OpenCodeRunWire(billed_zero=True))
    assert zero["cost"] == 0
    assert zero["tokens"] == billed["tokens"]
    assert zero["tokens"]["input"] > 0


def test_the_transcript_reports_a_synthesized_cost_by_default(tmp_path: Path) -> None:
    assistant = _assistant(tmp_path, billed_zero=False)
    finish = next(p for p in assistant["parts"] if p["type"] == "step-finish")
    assert finish["cost"] > 0
    assert assistant["info"]["cost"] > 0


def test_the_transcript_reports_zero_cost_and_keeps_tokens_when_billed_zero(tmp_path: Path) -> None:
    assistant = _assistant(tmp_path, billed_zero=True)
    finish = next(p for p in assistant["parts"] if p["type"] == "step-finish")
    assert finish["cost"] == 0
    assert assistant["info"]["cost"] == 0
    assert finish["tokens"]["output"] > 0
    assert assistant["info"]["tokens"] == finish["tokens"]


@pytest.mark.parametrize(("value", "expected"), [(None, False), ("0", False), ("true", False), ("1", True)])
def test_only_the_exact_value_one_turns_billed_zero_on(value: str | None, expected: bool) -> None:
    env = {} if value is None else {BILLED_ZERO_ENV_VAR: value}
    assert billed_zero_mode(env) is expected
