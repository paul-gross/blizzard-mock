"""OpenCode-shaped session-export transcript writer, for the
``opencode`` facade's ``run`` subcommand. Implements
:class:`~blizzard_mock.harness.engine.ITranscriptWriter`; unlike Claude Code's
append-only JSONL, real ``opencode export <id>`` returns one JSON document, so this
writer rewrites the whole file on every mutation."""

from __future__ import annotations

import json
import time
import uuid
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from blizzard_mock.harness.engine import ITranscriptWriter, RunResult
from blizzard_mock.harness.facades._text import render_ask_text
from blizzard_mock.harness.facades._transcript import TRANSCRIPTS_ROOT_ENV_VAR, transcripts_root
from blizzard_mock.harness.facades._usage import synthesize_cost_usd, synthesize_usage_tokens

__all__ = [
    "CHILD_TOOL_CALLS_KEY",
    "PROJECT_DIR_NAME",
    "TRANSCRIPTS_ROOT_ENV_VAR",
    "OpenCodeTranscriptWriter",
    "document_path",
    "transcripts_root",
]

#: The subdirectory every session document is grouped under — a single well-known path.
PROJECT_DIR_NAME = "mock-opencode"

#: The pinned OpenCode version these export documents claim.
_MOCK_VERSION = "1.18.25"

#: The reserved ``task`` input key a script uses to give the child session its own tool calls — a list of
#: ``{"tool": ..., "input": {...}, "output": ...}`` mappings. It never reaches the recorded tool part's input.
CHILD_TOOL_CALLS_KEY = "child_tool_calls"

#: The step-finish "reason" a completed turn reports; any nonempty string is faithful.
_STEP_REASON = "stop"


def _now_ms() -> int:
    return time.time_ns() // 1_000_000


def _new_id(prefix: str) -> str:
    """Mirrors ``opencode.py``'s own ``_new_id`` — a short, prefixed, session-stable id."""
    return f"{prefix}_{uuid.uuid4().hex[:16]}"


def document_path(root: Path, session_id: str) -> Path:
    """Where one session's export document lives under ``root`` — the one place this
    writer's on-disk layout is decided; ``export`` reads the same path it names."""
    return root / PROJECT_DIR_NAME / f"{session_id}.json"


def _tokens_field(usage: Mapping[str, int]) -> dict[str, object]:
    """The ``tokens`` shape both a ``step-finish`` part and a completed assistant
    message's own ``info.tokens`` carry — the exact field mapping ``opencode.py``'s
    ``_step_finish_event`` already uses, copied rather than reinvented; ``reasoning``
    has no Claude-shaped source, so it is always ``0``."""
    return {
        "input": usage["input_tokens"],
        "output": usage["output_tokens"],
        "reasoning": 0,
        "cache": {"read": usage["cache_read_input_tokens"], "write": usage["cache_creation_input_tokens"]},
    }


class OpenCodeTranscriptWriter:
    """Keeps one session's ``{info, messages}`` document in memory, rewriting the whole
    file on every mutation. A ``task`` tool call also mints a linked child document."""

    def __init__(
        self,
        *,
        session_id: str,
        root: Path,
        cwd: Path,
        on_task_completed: Callable[[Mapping[str, Any]], None] | None = None,
    ) -> None:
        self._session_id = session_id
        # Told each ``task`` part as it completes, so a facade can stream it as a ``tool_use`` event.
        self._on_task_completed = on_task_completed
        self._cwd = cwd
        self._root = root
        self._dir = root / PROJECT_DIR_NAME
        self._path = document_path(root, session_id)
        # A resume is a fresh process re-opening the same session id — load whatever this
        # session already wrote, else every resume would silently wipe its own history.
        self._doc: dict[str, Any] = (
            json.loads(self._path.read_text())
            if self._path.is_file()
            else _new_document(session_id, cwd, title="mock-opencode session")
        )
        # The in-progress assistant message a run of ``record_tool_call`` calls is
        # accumulating into, closed by the next ``record_user`` or ``record_result``.
        self._open_assistant: dict[str, Any] | None = None

    @property
    def path(self) -> Path:
        """The JSON export document this writer rewrites on every mutation."""
        return self._path

    def record_user(self, text: str) -> None:
        self._open_assistant = None  # a new user turn starts a fresh assistant turn
        message = self._new_message("user")
        message["parts"].append(self._new_part(message, {"type": "text", "text": text, "time": _span()}))
        self._doc["messages"].append(message)
        self._write()

    def record_result(self, result: RunResult) -> None:
        text = render_ask_text(result) if result.subtype == "ask" else result.text
        message = self._assistant_message()
        message["parts"].append(self._new_part(message, {"type": "text", "text": text, "time": _span()}))
        usage = synthesize_usage_tokens(text)
        message["parts"].append(
            self._new_part(
                message,
                {
                    "type": "step-finish",
                    "reason": _STEP_REASON,
                    "tokens": _tokens_field(usage),
                    "cost": synthesize_cost_usd(usage),
                },
            )
        )
        message["info"]["tokens"] = _tokens_field(usage)
        message["info"]["cost"] = synthesize_cost_usd(usage)
        self._open_assistant = None  # the turn is closed; the next tool call opens a new one
        self._write()

    def record_tool_call(self, name: str, tool_input: Mapping[str, object]) -> str:
        message = self._assistant_message()
        call_id = _new_id("call")
        started = _now_ms()
        recorded = {key: value for key, value in tool_input.items() if key != CHILD_TOOL_CALLS_KEY}
        state: dict[str, Any] = {"status": "pending", "input": recorded, "time": {"start": started}}
        if name == "task":
            # 1.18.32's shape: the child session is named by ``metadata.sessionId``, and a
            # ``task_id`` input continues that child rather than minting a new one.
            child_id = str(tool_input.get("task_id") or _new_id("ses"))
            state["metadata"] = {"parentSessionId": self._session_id, "sessionId": child_id, "truncated": False}
            child_calls = tool_input.get(CHILD_TOOL_CALLS_KEY) or []
            assert isinstance(child_calls, list)
            self._write_child_session(child_id, recorded, child_calls, started_at=started)
        part = self._new_part(message, {"type": "tool", "callID": call_id, "tool": name, "state": state})
        message["parts"].append(part)
        self._write()
        return call_id

    def record_tool_result(self, tool_use_id: str, output: str) -> None:
        # Search backward: the most recent still-pending call with this id answers it.
        for message in reversed(self._doc["messages"]):
            for part in reversed(message["parts"]):
                if part.get("type") != "tool" or part.get("callID") != tool_use_id:
                    continue
                if part["state"]["status"] != "pending":
                    continue
                part["state"]["status"] = "completed"
                part["state"]["output"] = output
                part["state"]["time"]["end"] = max(_now_ms(), part["state"]["time"]["start"])
                self._write()
                if part["tool"] == "task" and self._on_task_completed is not None:
                    self._on_task_completed(part)
                return

    # -- internals ------------------------------------------------------------ #

    def _assistant_message(self) -> dict[str, Any]:
        """The in-progress assistant message, minting one (with its own leading
        ``step-start`` part, matching the real per-turn shape) if none is open."""
        if self._open_assistant is None:
            message = self._new_message("assistant")
            message["parts"].append(self._new_part(message, {"type": "step-start"}))
            self._doc["messages"].append(message)
            self._open_assistant = message
        return self._open_assistant

    def _new_message(
        self, role: str, *, session_id: str | None = None, created_at: int | None = None
    ) -> dict[str, Any]:
        """A message under ``session_id`` — this writer's own session by default, or an
        explicit one for the child document :meth:`_write_child_session` builds — created
        at ``created_at`` (else now)."""
        return {
            "info": {
                "id": _new_id("msg"),
                "sessionID": session_id if session_id is not None else self._session_id,
                "role": role,
                "time": {"created": _now_ms() if created_at is None else created_at},
            },
            "parts": [],
        }

    def _new_part(
        self, message: Mapping[str, Any], fields: Mapping[str, Any], *, session_id: str | None = None
    ) -> dict[str, Any]:
        part: dict[str, Any] = {
            "id": _new_id("prt"),
            "sessionID": session_id if session_id is not None else self._session_id,
            "messageID": message["info"]["id"],
        }
        part.update(fields)
        return part

    def _write(self) -> None:
        self._dir.mkdir(parents=True, exist_ok=True)
        self._path.write_text(json.dumps(self._doc))

    def _write_child_session(
        self,
        child_id: str,
        tool_input: Mapping[str, object],
        child_calls: list[Mapping[str, Any]],
        *,
        started_at: int,
    ) -> None:
        """One user turn seeded from the parent call's ``prompt`` and one assistant reply, appended to
        the child's document (a ``task_id`` continues an existing one). The reply carries a completed
        tool part for each of ``child_calls``, ahead of its text. Created no earlier than the
        parent call's start, so they fall inside that call's window."""
        prompt = tool_input.get("prompt", "")
        child_path = document_path(self._root, child_id)
        doc = (
            json.loads(child_path.read_text())
            if child_path.is_file()
            else _new_document(child_id, self._cwd, title="mock-opencode child session", parent_id=self._session_id)
        )

        user = self._new_message("user", session_id=child_id, created_at=started_at)
        user["parts"].append(
            self._new_part(user, {"type": "text", "text": str(prompt), "time": _span(started_at)}, session_id=child_id)
        )

        assistant = self._new_message("assistant", session_id=child_id, created_at=started_at)
        for call in child_calls:
            assistant["parts"].append(
                self._new_part(
                    assistant,
                    {
                        "type": "tool",
                        "callID": _new_id("call"),
                        "tool": str(call["tool"]),
                        "state": {
                            "status": "completed",
                            "input": dict(call.get("input", {})),
                            "output": str(call.get("output", "ok")),
                            "time": _span(started_at),
                        },
                    },
                    session_id=child_id,
                )
            )
        reply_text = "the child session completed its delegated task"
        assistant["parts"].append(
            self._new_part(
                assistant, {"type": "text", "text": reply_text, "time": _span(started_at)}, session_id=child_id
            )
        )
        usage = synthesize_usage_tokens(reply_text, base_input=50, base_output=10)
        assistant["parts"].append(
            self._new_part(
                assistant,
                {
                    "type": "step-finish",
                    "reason": _STEP_REASON,
                    "tokens": _tokens_field(usage),
                    "cost": synthesize_cost_usd(usage),
                },
                session_id=child_id,
            )
        )
        assistant["info"]["tokens"] = _tokens_field(usage)
        assistant["info"]["cost"] = synthesize_cost_usd(usage)
        doc["messages"].extend([user, assistant])

        child_path.parent.mkdir(parents=True, exist_ok=True)
        child_path.write_text(json.dumps(doc))


def _span(started_at: int | None = None) -> dict[str, int]:
    """A part's ``time`` — an instant-long span starting at ``started_at`` (else now)."""
    start = _now_ms() if started_at is None else started_at
    return {"start": start, "end": start}


def _new_document(session_id: str, cwd: Path, *, title: str, parent_id: str | None = None) -> dict[str, Any]:
    info: dict[str, Any] = {
        "id": session_id,
        "version": _MOCK_VERSION,
        "directory": str(cwd),
        "title": title,
    }
    if parent_id is not None:
        info["parentID"] = parent_id
    return {"info": info, "messages": []}


# Typecheck-time Protocol conformance sentinel, matching the pattern
# ``blizzard-context:/exemplars/python/repo_pattern.py`` documents.
def _conforms_transcript_writer(x: OpenCodeTranscriptWriter) -> ITranscriptWriter:
    return x
