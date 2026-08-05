"""Claude-Code-shaped JSONL transcript writer — only the ``claude_code`` facade uses it.

Implements :class:`~blizzard_mock.harness.engine.ITranscriptWriter`. Assistant
records carry ``model``/``usage`` (epic #57), synthesized by :mod:`._usage`,
not a real token count.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path

from blizzard_mock.harness.engine import ITranscriptWriter, RunResult
from blizzard_mock.harness.facades._text import render_ask_text
from blizzard_mock.harness.facades._usage import MOCK_MODEL, synthesize_usage_tokens

#: Env var also read by the real runner; writer and reader must agree on this
#: name. Never falls back to a real ``~/.claude/projects``.
TRANSCRIPTS_ROOT_ENV_VAR = "BZ_TRANSCRIPTS_ROOT"

#: The subdirectory every session file is grouped under; the reader globs
#: ``<root>/*/<session_id>.jsonl``.
PROJECT_DIR_NAME = "mock-claude-code"


def transcripts_root(env: Mapping[str, str], *, fence_dir: Path) -> Path:
    """Where transcript files are written for this run.

    Reads the explicit :data:`TRANSCRIPTS_ROOT_ENV_VAR` override; when unset,
    falls back to a path under the fence rather than any real home directory.
    """
    override = env.get(TRANSCRIPTS_ROOT_ENV_VAR)
    if override:
        return Path(override)
    return fence_dir / ".blizzard-mock-harness" / "transcripts"


class ClaudeTranscriptWriter:
    """Appends Claude-Code-shaped JSONL records for one session.

    Opened in append mode per record, so spawn and a later ``--resume`` accumulate.
    """

    def __init__(self, *, session_id: str, root: Path, cwd: Path) -> None:
        self._session_id = session_id
        self._cwd = cwd
        self._path = root / PROJECT_DIR_NAME / f"{session_id}.jsonl"

    @property
    def path(self) -> Path:
        """The JSONL file this writer appends to — the one place its location is decided."""
        return self._path

    def record_user(self, text: str) -> None:
        self._append("user", {"role": "user", "content": text})

    def record_result(self, result: RunResult) -> None:
        text = render_ask_text(result) if result.subtype == "ask" else result.text
        usage = synthesize_usage_tokens(text)
        content = [{"type": "text", "text": text}]
        message = {"role": "assistant", "model": MOCK_MODEL, "usage": usage, "content": content}
        self._append("assistant", message)

    def record_tool_call(self, name: str, tool_input: Mapping[str, object]) -> str:
        tool_use_id = f"toolu_{uuid.uuid4().hex}"
        content = [{"type": "tool_use", "id": tool_use_id, "name": name, "input": dict(tool_input)}]
        # A smaller mid-turn usage figure than the closing text turn (`_usage.py`'s
        # lower bases).
        usage = synthesize_usage_tokens(name, base_input=50, base_output=10)
        self._append("assistant", {"role": "assistant", "model": MOCK_MODEL, "usage": usage, "content": content})
        return tool_use_id

    def record_tool_result(self, tool_use_id: str, output: str) -> None:
        content = [{"type": "tool_result", "tool_use_id": tool_use_id, "content": output}]
        self._append("user", {"role": "user", "content": content})

    def _append(self, record_type: str, message: dict[str, object]) -> None:
        record = {
            "type": record_type,
            "sessionId": self._session_id,
            "cwd": str(self._cwd),
            "timestamp": datetime.now(UTC).isoformat(),
            "message": message,
        }
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record) + "\n")


# Typecheck-time Protocol conformance sentinel, matching the pattern
# ``blizzard-context:/exemplars/python/repo_pattern.py`` documents.
def _conforms_transcript_writer(x: ClaudeTranscriptWriter) -> ITranscriptWriter:
    return x
