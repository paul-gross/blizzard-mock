"""Persisted mock-harness session state (framework-free core).

A real coding harness persists a session so a headless ``--resume`` can pick a
conversation back up; the mock does the same so a resumed behavior-script can
read *what it asked* and act on the answer it was resumed with
(``blizzard-discovery`` ``implementation/mocking.md``). State is a small JSON
document keyed by session id — no ORM, no server, importable at the unit tier.
"""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path


@dataclass
class Ask:
    """One ask-and-exit event: what the worker asked before parking."""

    question: str
    options: list[str] = field(default_factory=list)
    asked_at: float = field(default_factory=time.time)


@dataclass
class SessionState:
    """The durable state of one mock-harness session, keyed by ``session_id``.

    ``turns`` counts spawn + each resume; ``asks`` is every ask fired across the
    session's life; ``resumes`` is the sequence of resume messages delivered —
    what the *human* said, so a ``<behavior-script>``-tagged resume records its
    prose alone and an untagged one (code end to end) the whole raw message;
    ``verdicts`` is every verdict emitted. A resumed script reads
    ``asks``/``resumes`` to reconstruct context.
    """

    session_id: str
    turns: int = 0
    asks: list[Ask] = field(default_factory=list)
    resumes: list[str] = field(default_factory=list)
    verdicts: list[str] = field(default_factory=list)

    @property
    def last_ask(self) -> Ask | None:
        return self.asks[-1] if self.asks else None

    @property
    def last_answer(self) -> str | None:
        """The most recent resume message — the answer this turn was resumed with."""
        return self.resumes[-1] if self.resumes else None

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=2, sort_keys=True)

    @classmethod
    def from_json(cls, text: str) -> SessionState:
        raw = json.loads(text)
        asks = [Ask(**a) for a in raw.pop("asks", [])]
        return cls(asks=asks, **raw)


class SessionStore:
    """File-backed session persistence: one ``<session_id>.json`` per session.

    Kept dependency-free (plain files) so spawn and a later resume — two
    separate processes — share state through the filesystem, exactly as a real
    harness's session directory does.
    """

    def __init__(self, root: Path) -> None:
        self._root = root

    def _path(self, session_id: str) -> Path:
        # Session ids are uuids/hints; guard against path traversal regardless.
        safe = session_id.replace("/", "_").replace("..", "_")
        return self._root / f"{safe}.json"

    def load(self, session_id: str) -> SessionState | None:
        path = self._path(session_id)
        if not path.exists():
            return None
        return SessionState.from_json(path.read_text())

    def load_or_create(self, session_id: str) -> SessionState:
        return self.load(session_id) or SessionState(session_id=session_id)

    def save(self, state: SessionState) -> None:
        self._root.mkdir(parents=True, exist_ok=True)
        self._path(state.session_id).write_text(state.to_json())
