"""The fake service's sessions, one worker thread per running session, and the operations a turn is built from:
assistant steps, tool parts judged by the session's rules, permission requests, forms, child sessions, and the idle
that ends a turn. What a turn does belongs to the :class:`TurnRunner` the service is given. Sessions and messages
persist across service lifetimes; a session's environment does not, as in 2.1.4."""

from __future__ import annotations

import base64
import json
import os
import queue
import signal
import subprocess
import sys
import threading
from collections import deque
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol, cast

from ..domain import records, rules
from ..levers import Lever

#: The pause before each step, so a running turn is observable between polls.
STEP_SECONDS = 0.3
#: How long a parent holds after its child's interrupt, so an interrupt of the parent sent next still lands mid-turn.
AFTER_CHILD_INTERRUPT_SECONDS = 1.5

type Json = dict[str, object]


class NotFoundError(KeyError):
    pass


class SessionNotFoundError(NotFoundError):
    """A session the store does not hold, which 2.1.4 answers by name."""


class InvalidCursorError(ValueError):
    pass


#: 2.1.4's page when a listing names no limit, and the largest it serves.
DEFAULT_PAGE_LIMIT = 50
MAX_PAGE_LIMIT = 200


def encode_cursor(message_id: str, order: str, direction: str = "next") -> str:
    """2.1.4's opaque listing cursor: unpadded base64url JSON naming a page's edge message, its order and direction."""
    raw = json.dumps({"id": message_id, "order": order, "direction": direction}, separators=(",", ":"))
    return base64.urlsafe_b64encode(raw.encode()).decode().rstrip("=")


def decode_cursor(cursor: str) -> tuple[str, str]:
    try:
        decoded = json.loads(base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)))
        message_id, order, direction = str(decoded["id"]), str(decoded["order"]), decoded["direction"]
    except (ValueError, KeyError, TypeError) as exc:
        raise InvalidCursorError("Invalid cursor") from exc
    if direction != "next":
        raise InvalidCursorError("the mock pages forward only")
    return message_id, order


class ConflictError(ValueError):
    pass


@dataclass
class _Ask:
    request: Json
    answered: threading.Event = field(default_factory=threading.Event)
    decision: str | None = None


@dataclass
class _Form:
    record: Json
    settled: threading.Event = field(default_factory=threading.Event)


@dataclass
class Session:
    id: str
    directory: str
    metadata: Json
    permissions: list[Json]
    model: Json | None
    title: str | None
    agent: str | None
    parent_id: str | None = None
    created: int = field(default_factory=records.now_ms)
    updated: int = field(default_factory=records.now_ms)
    idle_at: int | None = None
    outcome: str | None = None
    messages: list[Json] = field(default_factory=list[Json])
    running: bool = False
    current: str | None = None
    environment: dict[str, str] | None = None
    pending: deque[tuple[str, str]] = field(default_factory=deque[tuple[str, str]])
    inbox: list[Json] = field(default_factory=list[Json])
    asks: dict[str, _Ask] = field(default_factory=dict[str, _Ask])
    forms: dict[str, _Form] = field(default_factory=dict[str, _Form])
    cancel: threading.Event = field(default_factory=threading.Event)
    holder: subprocess.Popen[bytes] | None = None

    def effective_model(self) -> Json:
        return self.model if self.model is not None else dict(records.DEFAULT_MODEL)

    def record(self) -> Json:
        totals = {"input": 0, "output": 0, "reasoning": 0}
        cache = {"read": 0, "write": 0}
        cost = 0.0
        for message in self.messages:
            used = message.get("tokens")
            if message.get("type") in {"assistant", "compaction"} and isinstance(used, dict):
                used_tokens = cast(Json, used)
                for key in totals:
                    totals[key] += _count(used_tokens.get(key))
                cached = used_tokens.get("cache")
                if isinstance(cached, dict):
                    cached_tokens = cast(Json, cached)
                    for key in cache:
                        cache[key] += _count(cached_tokens.get(key))
                spent = message.get("cost")
                cost += spent if isinstance(spent, int | float) else 0
        tokens: Json = {**totals, "cache": cache}
        timing: Json = {"created": self.created, "updated": self.updated}
        if self.idle_at is not None:
            timing["idle"] = self.idle_at
        body: Json = {
            "id": self.id,
            "projectID": "global",
            "location": {"directory": self.directory},
            "metadata": self.metadata,
            "permissions": self.permissions,
            "model": self.effective_model(),
            "title": self.title,
            "time": timing,
            "tokens": tokens,
            "cost": cost,
        }
        if self.parent_id is not None:
            body["parentID"] = self.parent_id
        if self.outcome is not None:
            body["outcome"] = self.outcome
        if self.agent is not None:
            body["agent"] = self.agent
        return body

    def stored(self) -> Json:
        return {
            "record": self.record(),
            "messages": self.messages,
            "running": self.running,
            "current": self.current,
        }


class TurnRunner(Protocol):
    """What a turn does. Each call ends the turn it was handed — through :meth:`Engine.idle`, directly or by way of
    :meth:`Engine.interrupted` — before it returns."""

    def run(self, engine: Engine, session: Session, prompt: str) -> None:
        """Run ``prompt`` as the session's turn; also how a restarted service resumes the turn it was running."""
        ...

    def wake(self, engine: Engine, session: Session, child: Session) -> None:
        """Run the turn a background ``child``'s finish wakes its root for, its result already delivered."""
        ...


class Engine:
    def __init__(self, levers: frozenset[Lever], store: Path, restart: Callable[[], None], runner: TurnRunner) -> None:
        self.levers = levers
        self._store = store
        self._restart = restart
        self._runner = runner
        self.lock = threading.RLock()
        self.sessions: dict[str, Session] = {}
        self._subscribers: list[queue.Queue[Json]] = []
        self._seq = 0
        #: Each tool part's assistant step, by part id, so its events can name the step.
        self._owners: dict[str, str] = {}
        self._restarted_by_lever = False
        self._halted = False

    # -- persistence ------------------------------------------------------------------------------------------------

    def load(self) -> list[Session]:
        """Load the store; returns the sessions that were running when the last lifetime ended."""
        try:
            stored = json.loads(self._store.read_text())
        except (FileNotFoundError, ValueError):
            return []
        self._restarted_by_lever = bool(stored.get("restarted_by_lever"))
        interrupted: list[Session] = []
        for item in stored.get("sessions", []):
            record = item["record"]
            session = Session(
                id=record["id"],
                directory=record["location"]["directory"],
                metadata=record.get("metadata") or {},
                permissions=record.get("permissions") or [],
                model=record.get("model"),
                title=record.get("title"),
                agent=record.get("agent"),
                parent_id=record.get("parentID"),
                created=record["time"]["created"],
                updated=record["time"]["updated"],
                idle_at=record["time"].get("idle"),
                outcome=record.get("outcome"),
                messages=item.get("messages") or [],
                current=item.get("current"),
            )
            self.sessions[session.id] = session
            if item.get("running"):
                interrupted.append(session)
        return interrupted

    def save(self) -> None:
        with self.lock:
            payload = {
                "restarted_by_lever": self._restarted_by_lever,
                "sessions": [session.stored() for session in self.sessions.values()],
            }
            self._store.parent.mkdir(parents=True, exist_ok=True)
            staged = self._store.with_suffix(".tmp")
            staged.write_text(json.dumps(payload))
            os.replace(staged, self._store)

    def resume(self, interrupted: list[Session]) -> None:
        """Resume each turn the last lifetime was running, as 2.1.4 does: the tool it was in is marked aborted and the
        turn runs again, without the session's environment."""
        for session in interrupted:
            with self.lock:
                for message in session.messages:
                    if message.get("type") == "assistant" and "completed" not in _time(message):
                        _abort_assistant(message, "Tool execution interrupted")
                session.running = True
            threading.Thread(target=self._resume_worker, args=(session,), daemon=True).start()

    def halt(self) -> None:
        """Stop every turn's processes and persist the state as it is, running turns included."""
        with self.lock:
            self._halted = True
            for session in self.sessions.values():
                kill(session.holder)
            self.save()

    # -- events -----------------------------------------------------------------------------------------------------

    def subscribe(self) -> queue.Queue[Json]:
        subscriber: queue.Queue[Json] = queue.Queue()
        with self.lock:
            self._subscribers.append(subscriber)
        return subscriber

    def unsubscribe(self, subscriber: queue.Queue[Json]) -> None:
        with self.lock, suppress(ValueError):
            self._subscribers.remove(subscriber)

    def publish(
        self,
        kind: str,
        session: Session,
        data: Json | None = None,
        *,
        durable: bool = True,
        located: bool = True,
        **fields: object,
    ) -> None:
        """Send one event to every subscriber, enveloped as 2.1.4 envelopes ``kind``: ``durable`` events carry their
        place in the session's log, ``located`` ones the session's directory."""
        with self.lock:
            event: Json = {
                "id": records.new_id("evt"),
                "type": kind,
                "created": records.now_ms(),
                "data": {"sessionID": session.id, **(data or {}), **fields},
            }
            if located:
                event["location"] = {"directory": session.directory}
            if durable:
                self._seq += 1
                event["durable"] = {"aggregateID": session.id, "seq": self._seq, "version": 1}
            for subscriber in self._subscribers:
                subscriber.put(event)

    # -- operations -------------------------------------------------------------------------------------------------

    def get(self, session_id: str) -> Session:
        session = self.sessions.get(session_id)
        if session is None:
            raise SessionNotFoundError(session_id)
        return session

    def create(self, body: Json) -> Session:
        session_id = str(body.get("id") or records.new_id("ses"))
        location = body.get("location")
        directory = str(cast(Json, location).get("directory")) if isinstance(location, dict) else os.getcwd()
        model = body.get("model")
        with self.lock:
            if session_id in self.sessions:
                raise ConflictError(session_id)
            session = Session(
                id=session_id,
                directory=directory,
                metadata=_object(body.get("metadata")),
                permissions=_objects(body.get("permissions")),
                model=cast(Json, model) if isinstance(model, dict) else None,
                title=str(body["title"]) if body.get("title") is not None else records.default_title(),
                agent=str(body["agent"]) if body.get("agent") is not None else None,
                parent_id=str(body["parentID"]) if body.get("parentID") else None,
            )
            self.sessions[session_id] = session
            self.save()
        created = {k: v for k, v in session.record().items() if k not in {"id", "time", "tokens", "cost", "outcome"}}
        self.publish("session.created", session, created)
        return session

    def update(self, session_id: str, body: Json) -> None:
        with self.lock:
            session = self.get(session_id)
            if "permissions" in body:
                session.permissions = _objects(body["permissions"])
            if "metadata" in body:
                session.metadata = _object(body["metadata"])
            session.updated = records.now_ms()
            self.save()

    def remove(self, session_id: str) -> None:
        with self.lock:
            doomed = self._tree(session_id)
            for sid in doomed:
                self._cancel(self.sessions[sid])
            for sid in doomed:
                self.sessions.pop(sid, None)
            self.save()

    def children(self, parent_id: str | None) -> list[Json]:
        with self.lock:
            return [s.record() for s in self.sessions.values() if parent_id is None or s.parent_id == parent_id]

    def active(self) -> Json:
        with self.lock:
            return {sid: {"type": "running"} for sid, s in self.sessions.items() if s.running}

    def set_environment(self, session_id: str, variables: Json) -> None:
        with self.lock:
            self.get(session_id).environment = {str(k): str(v) for k, v in variables.items()}

    def prompt(self, session_id: str, text: str, delivery: str | None) -> Json:
        with self.lock:
            session = self.get(session_id)
            session.pending.append(("steer" if delivery == "steer" else "prompt", text))
            self._start(session)
            return {"sessionID": session_id, "delivery": delivery or "queue", "text": text}

    def compact(self, session_id: str) -> None:
        with self.lock:
            session = self.get(session_id)
            session.pending.append(("compact", ""))
            self._start(session)

    def interrupt(self, session_id: str) -> bool:
        with self.lock:
            session = self.get(session_id)
            if Lever.INTERRUPT_IGNORED in self.levers:
                return True
            was_running = session.running
            if was_running:
                self._cancel(session)
            return was_running

    def switch_model(self, session_id: str, model: Json) -> None:
        with self.lock:
            session = self.get(session_id)
            session.model = model
            session.messages.append({"id": records.new_id("msg"), "type": "model-switched", "model": model, **_now()})
            self.save()

    def switch_agent(self, session_id: str, agent: str) -> None:
        with self.lock:
            session = self.get(session_id)
            session.agent = agent
            session.messages.append({"id": records.new_id("msg"), "type": "agent-switched", "agent": agent, **_now()})
            self.save()

    def messages(
        self, session_id: str, order: str | None, limit: int | None, cursor: str | None
    ) -> tuple[list[Json], dict[str, str | None]]:
        """One page and its ``previous``/``next`` cursors — both ``None`` on an empty page — as 2.1.4 pages: a cursor
        carries its own order and is refused beside one. Only a ``next`` cursor is followed."""
        if cursor is not None and order is not None:
            raise InvalidCursorError("Cursor cannot be combined with order")
        after: str | None = None
        if cursor is not None:
            after, order = decode_cursor(cursor)
        order = order or "asc"
        size = DEFAULT_PAGE_LIMIT if limit is None else limit
        if not 1 <= size <= MAX_PAGE_LIMIT:
            raise ValueError(f"Expected a value less than or equal to {MAX_PAGE_LIMIT} at limit")
        with self.lock:
            listed = [json.loads(json.dumps(m)) for m in self.get(session_id).messages]
        if order == "desc":
            listed.reverse()
        if after is not None:
            ids = [m.get("id") for m in listed]
            listed = listed[ids.index(after) + 1 :] if after in ids else []
        page = listed[:size]
        if not page:
            return page, {"previous": None, "next": None}
        return page, {
            "previous": encode_cursor(str(page[0]["id"]), order, "previous"),
            "next": encode_cursor(str(page[-1]["id"]), order),
        }

    def message(self, session_id: str, message_id: str) -> Json:
        with self.lock:
            for message in self.get(session_id).messages:
                if message.get("id") == message_id:
                    return json.loads(json.dumps(message))
        raise NotFoundError(message_id)

    def inbox(self, session_id: str) -> list[Json]:
        with self.lock:
            return list(self.get(session_id).inbox)

    def permission_requests(self, session_id: str) -> list[Json]:
        with self.lock:
            session = self.get(session_id)
            return [ask.request for ask in session.asks.values() if not ask.answered.is_set()]

    def reply_permission(self, session_id: str, request_id: str, decision: str) -> None:
        with self.lock:
            ask = self.get(session_id).asks.get(request_id)
            if ask is None:
                raise NotFoundError(request_id)
            ask.decision = decision
            ask.answered.set()

    def forms(self, session_id: str) -> list[Json]:
        with self.lock:
            return [form.record for form in self.get(session_id).forms.values()]

    def create_form(self, session_id: str, body: Json) -> Json:
        with self.lock:
            session = self.get(session_id)
            form: Json = {
                "id": records.new_id("frm"),
                "sessionID": session_id,
                "title": body.get("title"),
                "fields": body.get("fields") or [],
            }
            session.forms[str(form["id"])] = _Form(form)
            return form

    def cancel_form(self, session_id: str, form_id: str) -> None:
        with self.lock:
            form = self.get(session_id).forms.pop(form_id, None)
            if form is None:
                raise NotFoundError(form_id)
            form.settled.set()

    # -- the building blocks of a turn ------------------------------------------------------------------------------

    def wait(self, session: Session, seconds: float) -> bool:
        """Sleep ``seconds`` unless the session is interrupted first; ``True`` when it was."""
        return session.cancel.wait(seconds)

    def hold(self, session: Session, process: subprocess.Popen[bytes] | None) -> None:
        """Make ``process`` (its whole group) what an interrupt of ``session`` ends; ``None`` releases it."""
        with self.lock:
            session.holder = process

    def open_step(self, session: Session) -> Json:
        """Start one assistant step — a message of its own, as 2.1.4 records each model call."""
        assistant: Json = {
            "id": records.new_id("msg"),
            "type": "assistant",
            "agent": session.agent or "build",
            "model": session.effective_model(),
            "content": [],
            "time": {"created": records.now_ms()},
            "tokens": {"input": 0, "output": 0, "reasoning": 0, "cache": {"read": 0, "write": 0}},
            "cost": 0,
        }
        with self.lock:
            session.messages.append(assistant)
            self.save()
        self.publish(
            "session.step.started",
            session,
            assistantMessageID=assistant["id"],
            agent=assistant["agent"],
            model=assistant["model"],
            started=_time(assistant)["created"],
        )
        return assistant

    def add_part(self, session: Session, assistant: Json, part: Json) -> None:
        with self.lock:
            _content(assistant).append(part)
            self.save()

    def close_step(
        self, session: Session, assistant: Json, *, tokens: Json, cost: float, finish: str, text: str = ""
    ) -> None:
        """End one assistant step with its usage; ``finish`` is ``stop`` for a turn's last, ``tool-calls`` otherwise."""
        with self.lock:
            if text:
                _content(assistant).append({"type": "text", "text": text})
            assistant.update({"tokens": tokens, "cost": cost, "finish": finish})
            _time(assistant).update({"completed": records.now_ms()})
            session.updated = records.now_ms()
            self.save()
        if text:
            self.text_ended(session, assistant, text)
        self.publish("session.usage.updated", session, durable=False, located=False, tokens=tokens, cost=cost)
        self._step_ended(session, assistant)

    def text_ended(self, session: Session, assistant: Json, text: str) -> None:
        """Publish a text part written to ``assistant``, numbered among its text parts."""
        ordinal = sum(1 for part in _content(assistant) if part.get("type") == "text") - 1
        self.publish("session.text.ended", session, assistantMessageID=assistant["id"], ordinal=ordinal, text=text)

    def _step_ended(self, session: Session, assistant: Json) -> None:
        self.publish(
            "session.step.ended",
            session,
            assistantMessageID=assistant["id"],
            cost=assistant.get("cost", 0),
            files=[],
            finish=assistant.get("finish"),
            tokens=assistant.get("tokens"),
        )

    def user_message(self, session: Session, text: str) -> None:
        with self.lock:
            session.current = text
            session.cancel.clear()
            session.messages.append({"id": records.new_id("msg"), "type": "user", "text": text, **_now()})
            self.save()

    def take_steer(self, session: Session) -> str | None:
        """The first steer waiting on ``session``, taken off its queue, or ``None``."""
        with self.lock:
            for index, (kind, text) in enumerate(session.pending):
                if kind == "steer":
                    del session.pending[index]
                    return text
        return None

    def open_tool(self, session: Session, assistant: Json, name: str, tool_input: Json) -> Json | None:
        """Add a running tool part to ``assistant``; ``None`` when the session's rules withhold ``name`` from the model,
        so the call is never made."""
        if rules.withheld(session.permissions, name):
            return None
        part: Json = {
            "id": records.new_id("call"),
            "type": "tool",
            "name": name,
            "executed": False,
            "state": {"status": "running", "input": tool_input},
            "time": {"created": records.now_ms()},
        }
        self.add_part(session, assistant, part)
        with self.lock:
            self._owners[str(part["id"])] = str(assistant["id"])
        self.publish(
            "session.tool.called",
            session,
            assistantMessageID=assistant["id"],
            executed=False,
            id=part["id"],
            input=tool_input,
        )
        return part

    def judge(
        self, session: Session, assistant: Json, part: Json, action: str, resource: str, *, ask: bool = False
    ) -> str | None:
        """Hold a tool call to the session's rules: ``allow``, or ``deny`` with the part already failed as refused, or
        ``None`` when the session was interrupted while a permission request held it. An ``ask`` — the rules', or one
        ``ask`` forces on a call they would allow — raises a request and waits for its reply; an ``always`` reply
        saves the request's pattern as an allow rule."""
        effect = rules.evaluate(session.permissions, action, resource)
        if ask and effect == "allow":
            effect = "ask"
        if effect == "deny":
            self.fail_tool(session, part, "permission.rejected", f"Permission denied: {action}")
            return "deny"
        if effect != "ask":
            return "allow"
        source: Json = {"type": "tool", "id": part["id"], "messageID": assistant["id"]}
        decision = self.request_permission(session, action, resource, source, holding=part)
        if decision is None:
            return None
        if decision == "reject":
            self.fail_tool(session, part, "permission.rejected", f"Permission rejected: {action}")
            return "deny"
        if decision == "always":
            with self.lock:
                session.permissions.append(
                    {"action": action, "resource": rules.save_pattern(resource), "effect": "allow"}
                )
        return "allow"

    def request_permission(
        self, session: Session, action: str, resource: str, source: Json, *, holding: Json | None = None
    ) -> str | None:
        """Raise a permission request and wait for its reply: ``once``, ``always``, or ``reject``; ``None`` when the
        session was interrupted first. ``holding`` is the tool part the request pends."""
        ask = _Ask(
            {
                "id": records.new_id("per"),
                "sessionID": session.id,
                "action": action,
                "resources": [resource],
                "save": [rules.save_pattern(resource)],
                "source": source,
            }
        )
        with self.lock:
            session.asks[str(ask.request["id"])] = ask
            if holding is not None:
                _state(holding)["status"] = "pending"
        self.publish("session.permission.asked", session, requestID=ask.request["id"])
        ask.answered.wait()
        with self.lock:
            session.asks.pop(str(ask.request["id"]), None)
            if holding is not None:
                _state(holding)["status"] = "running"
        if session.cancel.is_set() or ask.decision is None:
            return None
        return ask.decision

    def await_form(self, session: Session, title: str, fields: list[Json]) -> Json | None:
        """Raise a form and hold the turn until it is cancelled; the form, or ``None`` when the session was
        interrupted first."""
        form = self.create_form(session.id, {"title": title, "fields": fields})
        with self.lock:
            pending = session.forms.get(str(form["id"]))
        while pending is not None and not pending.settled.wait(0.1):
            if session.cancel.is_set():
                with self.lock:
                    session.forms.pop(str(form["id"]), None)
                return None
        return form

    def complete_tool(self, session: Session, part: Json, output: str, metadata: Json | None = None) -> None:
        with self.lock:
            _state(part).update(
                {
                    "status": "completed",
                    "content": [{"type": "text", "text": output}],
                    "metadata": metadata or {"exit": 0, "status": "completed", "truncated": False},
                }
            )
            _time(part).update({"ran": records.now_ms(), "completed": records.now_ms()})
            self.save()
        state = _state(part)
        self.publish(
            "session.tool.success",
            session,
            assistantMessageID=self._owners.get(str(part["id"])),
            content=state.get("content"),
            executed=False,
            id=part["id"],
            metadata=state.get("metadata"),
        )

    def fail_tool(self, session: Session, part: Json, kind: str, message: str) -> None:
        with self.lock:
            state = _state(part)
            state.update({"status": "error", "error": {"type": kind, "message": message}})
            state.pop("content", None)
            _time(part).update({"completed": records.now_ms()})
            self.save()
        self.publish(
            "session.tool.failed",
            session,
            assistantMessageID=self._owners.get(str(part["id"])),
            id=part["id"],
            error=_state(part).get("error"),
        )

    def restart_due(self) -> bool:
        """Under ``RESTARTS_MID_TURN``, the first shell tool of the store's life asks for a restart and then holds,
        so the restart ends this lifetime while the tool still runs."""
        with self.lock:
            if Lever.RESTARTS_MID_TURN not in self.levers or self._restarted_by_lever:
                return False
            self._restarted_by_lever = True
            self.save()
        self._restart()
        return True

    def spawn_child(self, session: Session, part: Json, prompt: str, *, title: str, background: bool) -> Session:
        """Start a ``general`` child session on ``prompt``, recorded on the spawning tool part as 2.1.4 records it."""
        child = self.create(
            {
                "id": records.new_id("ses"),
                "parentID": session.id,
                "location": {"directory": session.directory},
                "model": session.model,
                "title": title,
                "agent": "general",
            }
        )
        with self.lock:
            _state(part)["metadata"] = {"sessionID": child.id, "status": "running", "truncated": False}
            given = _state(part).get("input")
            _state(part)["input"] = {
                **_object(given),
                "agent": "general",
                "prompt": prompt,
                "background": background,
            }
        self.prompt(child.id, prompt, None)
        return child

    def settle_child(self, session: Session, part: Json, child: Session, *, background: bool) -> str | None:
        """Close the spawning part: at once for a ``background`` child, else once the child idles, with its final
        text. ``None`` when ``session`` was interrupted first; an unsucceeded child fails the part."""
        if background:
            self.complete_tool(
                session,
                part,
                f'<subagent sessionID="{child.id}" state="running">\n</subagent>',
                {"sessionID": child.id, "status": "running", "truncated": False},
            )
            return ""
        while child.running:
            if self.wait(session, 0.1):
                return None
        if child.outcome != "succeeded":
            self.fail_tool(session, part, "aborted", "Subagent interrupted")
            if self.wait(session, AFTER_CHILD_INTERRUPT_SECONDS):
                return None
            return ""
        reply = final_text(child)
        self.complete_tool(
            session,
            part,
            f'<subagent sessionID="{child.id}" state="completed">\n{reply}\n</subagent>',
            {"sessionID": child.id, "status": "completed", "truncated": False},
        )
        return reply

    def fail_turn(self, session: Session, assistant: Json, error: Json) -> None:
        """End the turn as failed with ``error``, recorded on the step it ended in and on the idle."""
        with self.lock:
            assistant.update({"error": error, "finish": "error"})
            _time(assistant).setdefault("completed", records.now_ms())
            self.save()
        self._step_ended(session, assistant)
        self.idle(session, "failed", error)

    def interrupted(self, session: Session, assistant: Json | None) -> None:
        with self.lock:
            if assistant is not None:
                _abort_assistant(assistant, "Tool execution interrupted")
            self.save()
        self.idle(session, "interrupted")

    def idle(self, session: Session, outcome: str, error: Json | None = None) -> None:
        with self.lock:
            idle: Json = {"id": records.new_id("msg"), "type": "idle", "outcome": outcome, **_now()}
            if error is not None:
                idle["error"] = error
            session.messages.append(idle)
            session.outcome = outcome
            session.idle_at = records.now_ms()
            session.updated = session.idle_at
            session.current = None
            if outcome == "interrupted":
                session.pending.clear()
            self.save()
        suffix = {"succeeded": "succeeded", "interrupted": "interrupted"}.get(outcome, "failed")
        self.publish(f"session.execution.{suffix}", session, located=False)
        if session.parent_id is not None:
            self._child_settled(session)

    # -- the worker -------------------------------------------------------------------------------------------------

    def _tree(self, root_id: str) -> list[str]:
        ordered = [root_id] if root_id in self.sessions else []
        index = 0
        while index < len(ordered):
            ordered += [sid for sid, s in self.sessions.items() if s.parent_id == ordered[index]]
            index += 1
        return ordered

    def _cancel(self, session: Session) -> None:
        if session.running:
            session.cancel.set()
        session.pending.clear()
        session.inbox.clear()
        kill(session.holder)
        for ask in session.asks.values():
            ask.answered.set()
        for sid in self._tree(session.id)[1:]:
            child = self.sessions[sid]
            if child.running:
                self._cancel(child)

    def _start(self, session: Session) -> None:
        if session.running:
            return
        session.running = True
        session.cancel.clear()
        self.save()
        threading.Thread(target=self._worker, args=(session,), daemon=True).start()

    def _resume_worker(self, session: Session) -> None:
        if session.current is not None:
            self.publish("session.execution.started", session, located=False)
            self._runner.run(self, session, session.current)
        self._worker(session)

    def _worker(self, session: Session) -> None:
        while True:
            with self.lock:
                if self._halted or session.id not in self.sessions:
                    return
                if session.pending:
                    kind, text = session.pending.popleft()
                elif session.inbox:
                    kind, text = "inbox", ""
                else:
                    session.running = False
                    session.current = None
                    self.save()
                    return
            if kind == "compact":
                self._compact(session)
            elif kind == "inbox":
                self._deliver_inbox(session)
            else:
                self._turn(session, text)

    def _turn(self, session: Session, text: str) -> None:
        self.user_message(session, text)
        self.publish("session.execution.started", session, located=False)
        error = records.route_error(session.effective_model())
        if error is not None:
            self.idle(session, "failed", error)
            return
        self._runner.run(self, session, text)

    def _child_settled(self, child: Session) -> None:
        """A finished background child wakes its parent through the parent's inbox."""
        with self.lock:
            parent = self.sessions.get(child.parent_id or "")
            if parent is None or child.outcome != "succeeded":
                return
            spawned_in_background = any(
                part.get("name") == "subagent"
                and _object(_state(part).get("metadata")).get("sessionID") == child.id
                and _object(_state(part).get("input")).get("background") is True
                for message in parent.messages
                for part in _content(message)
            )
            if not spawned_in_background:
                return
            entry: Json = {
                "id": records.new_id("msg"),
                "type": "subagent",
                "childID": child.id,
                "text": final_text(child),
                "description": child.title,
            }
            parent.inbox.append(entry)
            self._start(parent)
        item = {key: value for key, value in entry.items() if key != "id"}
        self.publish("session.inbox.enqueued", parent, inboxID=entry["id"], item=item)

    def _deliver_inbox(self, session: Session) -> None:
        if self.wait(session, STEP_SECONDS):
            return
        with self.lock:
            if not session.inbox:
                return
            entry = session.inbox.pop(0)
            child = self.sessions.get(str(entry["childID"]))
            text = f'<subagent sessionID="{entry["childID"]}" state="completed" description="{entry["description"]}">'
            session.messages.append(
                {
                    "id": records.new_id("msg"),
                    "type": "synthetic",
                    "description": entry["description"],
                    "metadata": {"agent": "General", "childID": entry["childID"], "source": "subagent"},
                    "text": f"{text}\n{entry['text']}\n</subagent>",
                    **_now(),
                }
            )
            self.save()
        self.publish("session.inbox.delivered", session, inboxID=entry["id"])
        if child is None:
            self.idle(session, "succeeded")
            return
        self._runner.wake(self, session, child)

    def _compact(self, session: Session) -> None:
        if self.wait(session, STEP_SECONDS):
            self.idle(session, "interrupted")
            return
        tokens, cost = records.usage(session.effective_model(), "compact", "summary")
        with self.lock:
            session.messages.append(
                {
                    "id": records.new_id("msg"),
                    "type": "compaction",
                    "reason": "manual",
                    "status": "completed",
                    "model": session.effective_model(),
                    "summary": "## Objective\n- Continue the session.\n\n## Work State\n- (none)",
                    "tokens": tokens,
                    "cost": cost,
                    **_now(),
                }
            )
            self.save()
        self.idle(session, "succeeded")


def _count(value: object) -> int:
    return value if isinstance(value, int) else 0


def _now() -> Json:
    return {"time": {"created": records.now_ms()}}


def _object(value: object) -> Json:
    return dict(cast(Json, value)) if isinstance(value, dict) else {}


def _objects(value: object) -> list[Json]:
    if not isinstance(value, list):
        return []
    return [dict(cast(Json, item)) for item in cast("list[object]", value) if isinstance(item, dict)]


def _time(message: Json) -> Json:
    timing = message.setdefault("time", {})
    assert isinstance(timing, dict)
    return cast(Json, timing)


def _state(part: Json) -> Json:
    state = part.setdefault("state", {})
    assert isinstance(state, dict)
    return cast(Json, state)


def _content(message: object) -> list[Json]:
    content = cast(Json, message).get("content") if isinstance(message, dict) else None
    return cast("list[Json]", content) if isinstance(content, list) else []


def _abort_assistant(message: Json, tool_message: str) -> None:
    for part in _content(message):
        if part.get("type") == "tool" and _state(part).get("status") in {"running", "pending"}:
            _state(part).update({"status": "error", "error": {"type": "aborted", "message": tool_message}})
    message.update({"error": {"type": "aborted", "message": "Step interrupted"}, "finish": "error"})
    _time(message).setdefault("completed", records.now_ms())


def final_text(session: Session) -> str:
    """The text of ``session``'s last assistant step."""
    assistants = [m for m in session.messages if m.get("type") == "assistant"]
    parts = _content(assistants[-1]) if assistants else []
    return "\n".join(str(p.get("text")) for p in parts if p.get("type") == "text")


def kill(process: subprocess.Popen[bytes] | None) -> None:
    """End ``process`` and its whole process group, if it still runs."""
    if process is None or process.poll() is not None:
        return
    with suppress(ProcessLookupError):
        os.killpg(process.pid, signal.SIGKILL)
    process.wait()


def restart_in_background(argv0: str, log: Path) -> None:
    """Ask a detached ``service restart`` to end this lifetime and start the next."""
    log.parent.mkdir(parents=True, exist_ok=True)
    with log.open("ab") as sink:
        subprocess.Popen(
            [sys.executable, argv0, "service", "restart"],
            stdin=subprocess.DEVNULL,
            stdout=sink,
            stderr=sink,
            start_new_session=True,
        )


__all__ = [
    "ConflictError",
    "Engine",
    "NotFoundError",
    "Session",
    "TurnRunner",
    "final_text",
    "kill",
    "restart_in_background",
]
