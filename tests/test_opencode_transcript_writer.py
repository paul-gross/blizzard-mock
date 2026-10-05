"""Direct, in-process coverage for :class:`OpenCodeTranscriptWriter`.

``test_harness_smoke.py`` covers the facade end to end through a real subprocess;
these tests inspect the writer's own document shape and mutation-in-place behavior
directly, since a live pending-to-completed transition is otherwise unobservable
through the synchronous ``tool_call()`` helper (it calls both halves back to back).
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from blizzard_mock.harness.engine import RunResult
from blizzard_mock.harness.facades._opencode_transcript import OpenCodeTranscriptWriter, document_path


def _read(path: Path) -> dict:
    return json.loads(path.read_text())


def test_record_user_writes_the_root_document_with_a_matching_session_id(tmp_path: Path) -> None:
    writer = OpenCodeTranscriptWriter(session_id="sess-1", root=tmp_path, cwd=tmp_path)

    writer.record_user("do the thing")

    doc = _read(document_path(tmp_path, "sess-1"))
    assert doc["info"]["id"] == "sess-1"
    assert "parentID" not in doc["info"]
    assert len(doc["messages"]) == 1
    message = doc["messages"][0]
    assert message["info"]["role"] == "user"
    assert message["info"]["sessionID"] == "sess-1"
    assert message["parts"][0]["type"] == "text"
    assert message["parts"][0]["text"] == "do the thing"
    assert message["parts"][0]["sessionID"] == "sess-1"
    assert message["parts"][0]["messageID"] == message["info"]["id"]


def test_a_tool_call_is_pending_on_disk_until_its_result_lands(tmp_path: Path) -> None:
    writer = OpenCodeTranscriptWriter(session_id="sess-2", root=tmp_path, cwd=tmp_path)
    writer.record_user("go")

    call_id = writer.record_tool_call("bash", {"cmd": "ls"})
    mid_flight = _read(document_path(tmp_path, "sess-2"))
    tool_parts = [p for m in mid_flight["messages"] for p in m["parts"] if p.get("type") == "tool"]
    assert len(tool_parts) == 1
    assert tool_parts[0]["state"]["status"] == "pending"
    assert "output" not in tool_parts[0]["state"]
    assert tool_parts[0]["callID"] == call_id
    assert tool_parts[0]["state"]["input"] == {"cmd": "ls"}

    writer.record_tool_result(call_id, "file1\nfile2")
    after = _read(document_path(tmp_path, "sess-2"))
    tool_parts = [p for m in after["messages"] for p in m["parts"] if p.get("type") == "tool"]
    assert len(tool_parts) == 1
    assert tool_parts[0]["state"]["status"] == "completed"
    assert tool_parts[0]["state"]["output"] == "file1\nfile2"


def test_record_result_closes_the_turn_with_text_and_a_usage_bearing_step_finish(tmp_path: Path) -> None:
    writer = OpenCodeTranscriptWriter(session_id="sess-3", root=tmp_path, cwd=tmp_path)
    writer.record_user("go")

    writer.record_result(RunResult(session_id="sess-3", subtype="success", text="done here"))

    doc = _read(document_path(tmp_path, "sess-3"))
    assistant = next(m for m in doc["messages"] if m["info"]["role"] == "assistant")
    kinds = [p["type"] for p in assistant["parts"]]
    assert "text" in kinds
    assert "step-finish" in kinds
    text_part = next(p for p in assistant["parts"] if p["type"] == "text")
    assert text_part["text"] == "done here"
    finish_part = next(p for p in assistant["parts"] if p["type"] == "step-finish")
    assert finish_part["reason"]
    tokens = finish_part["tokens"]
    assert tokens["input"] > 0 and tokens["output"] > 0
    assert tokens["cache"]["read"] >= 0 and tokens["cache"]["write"] >= 0
    assert isinstance(finish_part["cost"], (int, float))
    assert assistant["info"]["tokens"] == tokens


def test_a_task_tool_call_mints_a_linked_child_document(tmp_path: Path) -> None:
    writer = OpenCodeTranscriptWriter(session_id="sess-4", root=tmp_path, cwd=tmp_path)
    writer.record_user("go")

    call_id = writer.record_tool_call("task", {"subagent_type": "explorer", "prompt": "dig in"})
    writer.record_tool_result(call_id, "delegated")

    root = _read(document_path(tmp_path, "sess-4"))
    task_part = next(p for m in root["messages"] for p in m["parts"] if p.get("type") == "tool")
    child_id = task_part["state"]["metadata"]["sessionId"]
    assert child_id != "sess-4"

    child = _read(document_path(tmp_path, child_id))
    assert child["info"]["id"] == child_id
    assert child["info"]["parentID"] == "sess-4"
    roles = [m["info"]["role"] for m in child["messages"]]
    assert roles == ["user", "assistant"]
    assert child["messages"][0]["parts"][0]["text"] == "dig in"


def test_a_task_call_can_carry_the_child_sessions_own_tool_calls(tmp_path: Path) -> None:
    writer = OpenCodeTranscriptWriter(session_id="sess-4b", root=tmp_path, cwd=tmp_path)
    writer.record_user("go")
    child_calls = [{"tool": "read", "input": {"filePath": "src/a.py"}, "output": "contents"}]

    call_id = writer.record_tool_call("task", {"prompt": "dig in", "child_tool_calls": child_calls})
    writer.record_tool_result(call_id, "delegated")

    root = _read(document_path(tmp_path, "sess-4b"))
    task_part = next(p for m in root["messages"] for p in m["parts"] if p.get("type") == "tool")
    assert task_part["state"]["input"] == {"prompt": "dig in"}
    child = _read(document_path(tmp_path, task_part["state"]["metadata"]["sessionId"]))
    [tool_part] = [p for m in child["messages"] for p in m["parts"] if p.get("type") == "tool"]
    assert tool_part["tool"] == "read"
    assert tool_part["state"]["status"] == "completed"
    assert tool_part["state"]["input"] == {"filePath": "src/a.py"}
    assert tool_part["state"]["output"] == "contents"
    assert tool_part["sessionID"] == child["info"]["id"]


def test_a_non_task_tool_call_mints_no_child_document(tmp_path: Path) -> None:
    writer = OpenCodeTranscriptWriter(session_id="sess-5", root=tmp_path, cwd=tmp_path)
    writer.record_user("go")

    call_id = writer.record_tool_call("bash", {"cmd": "ls"})
    writer.record_tool_result(call_id, "ok")

    doc = _read(document_path(tmp_path, "sess-5"))
    tool_part = next(p for m in doc["messages"] for p in m["parts"] if p.get("type") == "tool")
    assert "metadata" not in tool_part["state"]
    assert [p.name for p in (tmp_path / "mock-opencode").glob("*.json")] == ["sess-5.json"]


def test_every_message_and_part_id_is_unique_and_self_consistent(tmp_path: Path) -> None:
    writer = OpenCodeTranscriptWriter(session_id="sess-6", root=tmp_path, cwd=tmp_path)
    writer.record_user("go")
    call_id = writer.record_tool_call("bash", {"cmd": "ls"})
    writer.record_tool_result(call_id, "ok")
    writer.record_result(RunResult(session_id="sess-6", subtype="success", text="done"))

    doc = _read(document_path(tmp_path, "sess-6"))
    message_ids = [m["info"]["id"] for m in doc["messages"]]
    assert len(message_ids) == len(set(message_ids))
    part_ids = [p["id"] for m in doc["messages"] for p in m["parts"]]
    assert len(part_ids) == len(set(part_ids))
    for message in doc["messages"]:
        for part in message["parts"]:
            assert part["sessionID"] == "sess-6"
            assert part["messageID"] == message["info"]["id"]


def test_messages_parts_and_tool_states_carry_times_and_a_task_window_holds_its_child() -> None:
    import tempfile

    with tempfile.TemporaryDirectory() as raw:
        tmp_path = Path(raw)
        seen: list[Mapping[str, Any]] = []
        writer = OpenCodeTranscriptWriter(
            session_id="sess-7", root=tmp_path, cwd=tmp_path, on_task_completed=seen.append
        )
        writer.record_user("go")
        call_id = writer.record_tool_call("task", {"subagent_type": "explorer", "prompt": "dig in"})
        writer.record_tool_result(call_id, "delegated")

        root = _read(document_path(tmp_path, "sess-7"))
        assert all(isinstance(m["info"]["time"]["created"], int) for m in root["messages"])
        task = next(p for m in root["messages"] for p in m["parts"] if p.get("type") == "tool")
        window = task["state"]["time"]
        assert window["start"] <= window["end"]
        assert seen == [task]
        child = _read(document_path(tmp_path, task["state"]["metadata"]["sessionId"]))
        assert all(window["start"] <= m["info"]["time"]["created"] <= window["end"] for m in child["messages"])


def test_a_task_id_continues_the_same_child_document() -> None:
    import tempfile

    with tempfile.TemporaryDirectory() as raw:
        tmp_path = Path(raw)
        writer = OpenCodeTranscriptWriter(session_id="sess-8", root=tmp_path, cwd=tmp_path)
        writer.record_user("go")
        for _ in range(2):
            call_id = writer.record_tool_call("task", {"task_id": "ses_kid", "prompt": "again"})
            writer.record_tool_result(call_id, "ok")

        child = _read(document_path(tmp_path, "ses_kid"))
        assert [m["info"]["role"] for m in child["messages"]] == ["user", "assistant", "user", "assistant"]
