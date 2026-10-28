"""Persisted mock-harness session state (framework-free core).

A resumed behavior-script reads what it asked and the answer it was resumed
with. State is a small JSON document keyed by session id — no ORM, no server,
importable at the unit tier.
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
class Invocation:
    """One turn's observed model/effort/compaction-window/permission flags.

    ``None`` means the flag was absent from argv, not unknown.
    """

    kind: str  # spawn | resume
    model: str | None = None
    effort: str | None = None
    compaction_window: str | None = None
    permission: str | None = None


@dataclass
class SpawnCredential:
    """The lease credential the runner exported to the spawned worker.

    ``None`` means the variable was absent from the spawn's env.
    """

    lease_id: str | None = None
    lease_token: str | None = None
    runner_url: str | None = None


@dataclass
class SessionState:
    """The durable state of one mock-harness session, keyed by ``session_id``.

    ``invocations`` records the observed model/effort flag per turn.
    """

    session_id: str
    turns: int = 0
    asks: list[Ask] = field(default_factory=list)
    resumes: list[str] = field(default_factory=list)
    verdicts: list[str] = field(default_factory=list)
    invocations: list[Invocation] = field(default_factory=list)
    #: Set when the spawn carried the runner selftest's lease identity; a later resume
    #: arrives without it, so the session remembers it.
    selftest: bool = False
    #: Recorded at spawn only — a resume does not re-export it.
    spawn_credential: SpawnCredential | None = None

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
        invocations = [Invocation(**i) for i in raw.pop("invocations", [])]
        credential = raw.pop("spawn_credential", None)
        spawn_credential = SpawnCredential(**credential) if credential is not None else None
        return cls(asks=asks, invocations=invocations, spawn_credential=spawn_credential, **raw)


class SessionStore:
    """File-backed session persistence: one ``<session_id>.json`` per session.

    Dependency-free plain files, so spawn and resume share state via disk.
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
