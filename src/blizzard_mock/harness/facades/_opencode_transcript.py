"""OpenCode-shaped session-export transcript writer (blizzard#437), for the
``opencode`` facade's ``run`` subcommand. Implements
:class:`~blizzard_mock.harness.engine.ITranscriptWriter`; unlike Claude Code's
append-only JSONL, real ``opencode export <id>`` returns one JSON document, so this
writer rewrites the whole file on every mutation."""

from __future__ import annotations

import json
import uuid
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from blizzard_mock.harness.engine import ITranscriptWriter, RunResult
from blizzard_mock.harness.facades._text import render_ask_text
from blizzard_mock.harness.facades._transcript import TRANSCRIPTS_ROOT_ENV_VAR, transcripts_root
from blizzard_mock.harness.facades._usage import synthesize_cost_usd, synthesize_usage_tokens

__all__ = [
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

#: The step-finish "reason" a completed turn reports; any nonempty string is faithful.
_STEP_REASON = "stop"


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

    def __init__(self, *, session_id: str, root: Path, cwd: Path) -> None:
        self._session_id = session_id
        self._cwd = cwd
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
        message["parts"].append(self._new_part(message, {"type": "text", "text": text}))
        self._doc["messages"].append(message)
        self._write()

    def record_result(self, result: RunResult) -> None:
        text = render_ask_text(result) if result.subtype == "ask" else result.text
        message = self._assistant_message()
        message["parts"].append(self._new_part(message, {"type": "text", "text": text}))
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
        state: dict[str, Any] = {"status": "pending", "input": dict(tool_input)}
        if name == "task":
            # A fresh child session, linked only through the undocumented pointer.
            child_id = _new_id("ses")
            state["metadata"] = {"sessionID": child_id}
            self._write_child_session(child_id, tool_input)
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
                self._write()
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

    def _new_message(self, role: str) -> dict[str, Any]:
        return {"info": {"id": _new_id("msg"), "sessionID": self._session_id, "role": role}, "parts": []}

    def _new_part(self, message: Mapping[str, Any], fields: Mapping[str, Any]) -> dict[str, Any]:
        part: dict[str, Any] = {
            "id": _new_id("prt"),
            "sessionID": self._session_id,
            "messageID": message["info"]["id"],
        }
        part.update(fields)
        return part

    def _write(self) -> None:
        self._dir.mkdir(parents=True, exist_ok=True)
        self._path.write_text(json.dumps(self._doc))

    def _write_child_session(self, child_id: str, tool_input: Mapping[str, object]) -> None:
        """A trivial, complete child document — one user turn seeded from the parent
        call's own ``prompt`` (else empty), one assistant text reply — round-trippable
        through the real ``OpenCodeTranscriptSource``/``parse_session_export`` as a
        resolved sidechain."""
        prompt = tool_input.get("prompt", "")
        doc = _new_document(child_id, self._cwd, title="mock-opencode child session", parent_id=self._session_id)
        user = {"info": {"id": _new_id("msg"), "sessionID": child_id, "role": "user"}, "parts": []}
        user["parts"].append(
            {
                "id": _new_id("prt"),
                "sessionID": child_id,
                "messageID": user["info"]["id"],
                "type": "text",
                "text": str(prompt),
            }
        )
        assistant = {"info": {"id": _new_id("msg"), "sessionID": child_id, "role": "assistant"}, "parts": []}
        reply_text = "the child session completed its delegated task"
        assistant["parts"].append(
            {
                "id": _new_id("prt"),
                "sessionID": child_id,
                "messageID": assistant["info"]["id"],
                "type": "text",
                "text": reply_text,
            }
        )
        usage = synthesize_usage_tokens(reply_text, base_input=50, base_output=10)
        assistant["parts"].append(
            {
                "id": _new_id("prt"),
                "sessionID": child_id,
                "messageID": assistant["info"]["id"],
                "type": "step-finish",
                "reason": _STEP_REASON,
                "tokens": _tokens_field(usage),
                "cost": synthesize_cost_usd(usage),
            }
        )
        assistant["info"]["tokens"] = _tokens_field(usage)
        assistant["info"]["cost"] = synthesize_cost_usd(usage)
        doc["messages"] = [user, assistant]
        child_dir = self._dir
        child_dir.mkdir(parents=True, exist_ok=True)
        (child_dir / f"{child_id}.json").write_text(json.dumps(doc))


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
