"""Claude-Code-shaped JSONL transcript writer — only the ``claude_code`` facade uses it.

Mints the same record shapes the real runner's transcript normalizer
(``blizzard/runner/harness/internal/claude_code_normalizer.py``, blizzard#245 — the
successor to the old ``transcripts/parser.py``) reads, so a chunk that runs through
the mock fleet produces a conversation the runner panel can open. Only Claude Code has
a reader today (codex/opencode have none), so only :mod:`~blizzard_mock.harness.
facades.claude_code` constructs one; the engine's ``transcript`` parameter
(:class:`~blizzard_mock.harness.engine.ITranscriptWriter`) is left ``None``
everywhere else and the shared engine no-ops.

Implements :class:`~blizzard_mock.harness.engine.ITranscriptWriter`: the engine
calls into it at two defined points (the spawn/resume user turn, the final
assistant result) and never renders anything itself; the ``harness/helpers.py``
tool-call surface drives ``record_tool_call``/``record_tool_result`` off the run
context in between — the package README's "Conversation transcripts" owns which
helpers those are.

Minted deliberately narrow: ``sessionId``/``cwd``/``timestamp`` ride every record
for a human reading the file, even though normalization does not need them for the
plain conversation shape this writer mints (only ``type`` and ``message.content`` in
file order). Left out entirely — a **documented gap**, not a claim the normalizer
does not want them: it added a ``uuid``/``parentUuid`` chain (inline-sidechain
threading) and sidecar-file discovery (``isSidechain`` subagent conversations,
``<session-id>/subagents/agent-<agentId>.jsonl``) that this writer deliberately never
mints, alongside the pre-existing gaps below. Recorded at
``blizzard-context:/verification/blizzard.md`` rather than invented around here; it
closes when a future issue teaches this writer to mint those shapes. Still absent
regardless: the ``<persisted-output>`` large-result offload wrapper, and byte-exact
ANSI fidelity.

Every assistant-type record also carries ``model`` + ``usage`` (blizzard epic #57)
— the runner adapter's transcript-summation fallback
(``blizzard.runner.harness.internal.claude_code_adapter.ClaudeCodeAdapter.
sum_transcript_usage``) reads exactly these two keys off each ``type: "assistant"``
record's ``message``, so both the final result turn (:meth:`ClaudeTranscriptWriter.
record_result`) and each mid-turn tool-call turn (:meth:`ClaudeTranscriptWriter.
record_tool_call`) mint them. Figures are :mod:`._usage`'s deterministic synthesis,
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

#: Env var the runner also reads (``blizzard.runner.config.ENV_TRANSCRIPTS_ROOT``) —
#: writer and reader must agree on this name for a mock-minted transcript to be
#: found. **The runner-side default when this is unset is the developer's real
#: ``~/.claude/projects`` — the mock must never fall back to that** (see
#: :func:`transcripts_root`).
TRANSCRIPTS_ROOT_ENV_VAR = "BZ_TRANSCRIPTS_ROOT"

#: The subdirectory every session file is grouped under. The real reader locates a
#: transcript by globbing ``<root>/*/<session_id>.jsonl`` and only consults the
#: directory name as a multi-match tie-break — which a UUID4 session id never
#: triggers (``blizzard/runner/harness/internal/claude_code_transcript.py``) — so one
#: stable name is enough; replicating Claude Code's mangled-cwd directory naming buys
#: nothing.
PROJECT_DIR_NAME = "mock-claude-code"


def transcripts_root(env: Mapping[str, str], *, fence_dir: Path) -> Path:
    """Where transcript files are written for this run.

    Reads the explicit :data:`TRANSCRIPTS_ROOT_ENV_VAR` override; when unset, falls
    back to a path **under the fence** (beside the session-state directory,
    ``engine.fence_base_dir``) rather than any real home directory. The runner
    resolves its own unset case to ``~/.claude/projects`` — the developer's actual
    Claude Code session store — and the mock must never write there, silently or
    otherwise.
    """
    override = env.get(TRANSCRIPTS_ROOT_ENV_VAR)
    if override:
        return Path(override)
    return fence_dir / ".blizzard-mock-harness" / "transcripts"


class ClaudeTranscriptWriter:
    """Appends Claude-Code-shaped JSONL records for one session.

    One file per session at ``<root>/<PROJECT_DIR_NAME>/<session_id>.jsonl``,
    opened in append mode per record so spawn and a later ``--resume`` (two
    separate processes) accumulate the same conversation, exactly as session
    state does (``harness/session.py``).
    """

    def __init__(self, *, session_id: str, root: Path, cwd: Path) -> None:
        self._session_id = session_id
        self._cwd = cwd
        self._path = root / PROJECT_DIR_NAME / f"{session_id}.jsonl"

    @property
    def path(self) -> Path:
        """The JSONL file this writer appends to.

        Read by ``claude_code.main`` for the hook payload's ``transcript_path``, so
        the composition below stays the one place the file's location is decided.
        """
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
        # lower bases) — a real tool-call turn's completion is shorter than the
        # turn's final message.
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
