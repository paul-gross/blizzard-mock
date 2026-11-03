"""The fake service's sessions and the turns they run, one worker thread per running session.

A turn answers its prompt's plan (``domain.script``) with fixed behavior: shell calls run the fake shell's grammar
against the session's environment and directory, a ``sleep`` holds a real stand-in process so the tool's pid can be
seen and stopped, rules are enforced as OpenCode 2.1.4 enforces them, and a subagent runs as a child session. Sessions
and messages persist across service lifetimes; a session's environment does not, as in 2.1.4."""

from __future__ import annotations

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

from ..domain import records, rules, script, shell
from ..levers import Lever

#: The longest a fake ``sleep`` holds its tool; long enough to be seen running, short enough for a test tier.
TOOL_SECONDS_CAP = 6.0
#: The pause before each step, so a running turn is observable between polls.
STEP_SECONDS = 0.3
#: How long a parent waits after its child was interrupted before it carries on, so an interrupt of the parent
#: issued right after the child's still lands mid-turn.
AFTER_CHILD_INTERRUPT_SECONDS = 1.5

type Json = dict[str, object]


class NotFoundError(KeyError):
    pass


class ConflictError(ValueError):
    pass


@dataclass
class _Ask:
    request: Json
    answered: threading.Event = field(default_factory=threading.Event)
    decision: str | None = None


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
    forms: dict[str, Json] = field(default_factory=dict[str, Json])
    cancel: threading.Event = field(default_factory=threading.Event)
    holder: subprocess.Popen[bytes] | None = None

    def effective_model(self) -> Json:
        return self.model if self.model is not None else dict(records.DEFAULT_MODEL)

    def record(self) -> Json:
        totals = {"input": 0, "output": 0, "reasoning": 0}
        cost = 0.0
        for message in self.messages:
            used = message.get("tokens")
            if message.get("type") in {"assistant", "compaction"} and isinstance(used, dict):
                for key in totals:
                    count = used.get(key)
                    totals[key] += count if isinstance(count, int) else 0
                spent = message.get("cost")
                cost += spent if isinstance(spent, int | float) else 0
        tokens: Json = {**totals, "cache": {"read": 0, "write": 0}}
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


class Engine:
    def __init__(self, levers: frozenset[Lever], store: Path, restart: Callable[[], None]) -> None:
        self.levers = levers
        self._store = store
        self._restart = restart
        self.lock = threading.RLock()
        self.sessions: dict[str, Session] = {}
        self._subscribers: list[queue.Queue[Json]] = []
        self._seq = 0
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
        """Stop every stand-in process and persist the state as it is, running turns included."""
        with self.lock:
            self._halted = True
            for session in self.sessions.values():
                _kill(session.holder)
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

    def publish(self, kind: str, session: Session, **data: object) -> None:
        with self.lock:
            self._seq += 1
            event: Json = {
                "id": records.new_id("evt"),
                "type": kind,
                "created": records.now_ms(),
                "data": {"sessionID": session.id, **data},
                "location": {"directory": session.directory},
                "durable": {"aggregateID": session.id, "seq": self._seq, "version": 1},
            }
            for subscriber in self._subscribers:
                subscriber.put(event)

    # -- operations -------------------------------------------------------------------------------------------------

    def get(self, session_id: str) -> Session:
        session = self.sessions.get(session_id)
        if session is None:
            raise NotFoundError(session_id)
        return session

    def create(self, body: Json) -> Session:
        session_id = str(body.get("id") or records.new_id("ses"))
        location = body.get("location")
        directory = str(location.get("directory")) if isinstance(location, dict) else os.getcwd()
        model = body.get("model")
        with self.lock:
            if session_id in self.sessions:
                raise ConflictError(session_id)
            session = Session(
                id=session_id,
                directory=directory,
                metadata=_object(body.get("metadata")),
                permissions=_objects(body.get("permissions")),
                model=model if isinstance(model, dict) else None,
                title=str(body["title"]) if body.get("title") is not None else None,
                agent=str(body["agent"]) if body.get("agent") is not None else None,
                parent_id=str(body["parentID"]) if body.get("parentID") else None,
            )
            self.sessions[session_id] = session
            self.save()
        self.publish("session.created", session)
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

    def messages(self, session_id: str, order: str, limit: int | None, cursor: str | None) -> list[Json]:
        with self.lock:
            listed = [json.loads(json.dumps(m)) for m in self.get(session_id).messages]
        if order == "desc":
            listed.reverse()
        if cursor is not None:
            ids = [m.get("id") for m in listed]
            listed = listed[ids.index(cursor) + 1 :] if cursor in ids else []
        return listed[:limit] if limit is not None else listed

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
            return list(self.get(session_id).forms.values())

    def create_form(self, session_id: str, body: Json) -> Json:
        with self.lock:
            session = self.get(session_id)
            form: Json = {
                "id": records.new_id("frm"),
                "sessionID": session_id,
                "title": body.get("title"),
                "fields": body.get("fields") or [],
            }
            session.forms[str(form["id"])] = form
            return form

    def cancel_form(self, session_id: str, form_id: str) -> None:
        with self.lock:
            if self.get(session_id).forms.pop(form_id, None) is None:
                raise NotFoundError(form_id)

    # -- turns ------------------------------------------------------------------------------------------------------

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
        _kill(session.holder)
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
            self.publish("session.execution.started", session)
            self._execute(session, script.plan(session.current), session.current)
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

    def _wait(self, session: Session, seconds: float) -> bool:
        """Sleep ``seconds`` unless the session is interrupted first; ``True`` when it was."""
        return session.cancel.wait(seconds)

    def _turn(self, session: Session, text: str) -> None:
        with self.lock:
            session.current = text
            session.cancel.clear()
            session.messages.append({"id": records.new_id("msg"), "type": "user", "text": text, **_now()})
            self.save()
        self.publish("session.execution.started", session)
        error = records.route_error(session.effective_model())
        if error is not None:
            self._idle(session, "failed", error)
            return
        self._execute(session, script.plan(text), text)

    def _execute(self, session: Session, plan: script.Plan, prompt: str) -> None:
        model = session.effective_model()
        assistant: Json = {
            "id": records.new_id("msg"),
            "type": "assistant",
            "agent": session.agent or "build",
            "model": model,
            "content": [],
            "time": {"created": records.now_ms()},
            "tokens": {"input": 0, "output": 0, "reasoning": 0, "cache": {"read": 0, "write": 0}},
            "cost": 0,
        }
        with self.lock:
            session.messages.append(assistant)
            self.save()
        self.publish("session.step.started", session, messageID=assistant["id"])
        outputs: list[str] = []
        refused = False
        for step in plan.steps:
            if self._wait(session, STEP_SECONDS):
                self._interrupted(session, assistant)
                return
            outcome = self._tool(session, assistant, step)
            if outcome is None:
                self._interrupted(session, assistant)
                return
            output, was_refused = outcome
            outputs.append(output)
            refused = refused or was_refused
            steer = self._take_steer(session)
            if steer is not None:
                self._finish(session, assistant, "", prompt)
                with self.lock:
                    session.current = steer
                    session.messages.append({"id": records.new_id("msg"), "type": "user", "text": steer, **_now()})
                self._execute(session, script.plan(steer), steer)
                return
        if self._wait(session, STEP_SECONDS):
            self._interrupted(session, assistant)
            return
        reply = plan.reply(outputs, refused=refused)
        self._finish(session, assistant, reply, prompt)
        self._idle(session, "succeeded")

    def _take_steer(self, session: Session) -> str | None:
        with self.lock:
            for index, (kind, text) in enumerate(session.pending):
                if kind == "steer":
                    del session.pending[index]
                    return text
        return None

    def _finish(self, session: Session, assistant: Json, reply: str, prompt: str) -> None:
        tokens, cost = records.usage(session.effective_model(), prompt, reply)
        with self.lock:
            if reply:
                _content(assistant).append({"type": "text", "text": reply})
            assistant.update({"tokens": tokens, "cost": cost, "finish": "stop"})
            _time(assistant).update({"completed": records.now_ms()})
            session.updated = records.now_ms()
            self.save()
        self.publish("session.text.ended", session, messageID=assistant["id"])
        self.publish("session.usage.updated", session, messageID=assistant["id"])
        self.publish("session.step.ended", session, messageID=assistant["id"])

    def _interrupted(self, session: Session, assistant: Json) -> None:
        with self.lock:
            _abort_assistant(assistant, "Tool execution interrupted")
            self.save()
        self._idle(session, "interrupted")

    def _idle(self, session: Session, outcome: str, error: Json | None = None) -> None:
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
        self.publish(f"session.execution.{suffix}", session)
        if session.parent_id is not None:
            self._child_settled(session)

    # -- tools ------------------------------------------------------------------------------------------------------

    def _tool(self, session: Session, assistant: Json, step: script.ToolStep) -> tuple[str, bool] | None:
        """Run one tool call; ``(output, refused)``, or ``None`` when the session was interrupted during it."""
        name = "shell" if isinstance(step, script.Shell) else "read" if isinstance(step, script.Read) else "subagent"
        if rules.withheld(session.permissions, name):
            return "", True
        part: Json = {
            "id": records.new_id("call"),
            "type": "tool",
            "name": name,
            "executed": False,
            "state": {"status": "running", "input": _input(step)},
            "time": {"created": records.now_ms()},
        }
        with self.lock:
            _content(assistant).append(part)
            self.save()
        self.publish("session.tool.called", session, callID=part["id"], tool=name)
        if isinstance(step, script.Shell):
            result = self._shell(session, part, step.command, str(assistant["id"]))
        elif isinstance(step, script.Read):
            result = self._read(session, part, step.path)
        else:
            result = self._subagent(session, part, step)
        status = _state(part).get("status")
        self.publish(
            "session.tool.success" if status == "completed" else "session.tool.failed", session, callID=part["id"]
        )
        return result

    def _complete(self, part: Json, output: str, metadata: Json | None = None) -> None:
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

    def _fail(self, part: Json, kind: str, message: str) -> None:
        with self.lock:
            state = _state(part)
            state.update({"status": "error", "error": {"type": kind, "message": message}})
            state.pop("content", None)
            _time(part).update({"completed": records.now_ms()})
            self.save()

    def _shell(self, session: Session, part: Json, command: str, message_id: str) -> tuple[str, bool] | None:
        effect = rules.evaluate(session.permissions, "shell", command)
        if effect == "deny":
            self._fail(part, "permission.rejected", "Permission denied: shell")
            return "", True
        if effect == "ask":
            decision = self._ask(session, part, command, message_id)
            if decision is None:
                return None
            if decision == "reject":
                self._fail(part, "permission.rejected", "Permission rejected: shell")
                return "", True
            if decision == "always":
                with self.lock:
                    session.permissions.append(
                        {"action": "shell", "resource": rules.save_pattern(command), "effect": "allow"}
                    )
        env = dict(os.environ) if session.environment is None else dict(session.environment)
        holder = subprocess.Popen(["sleep", "3600"], start_new_session=True) if "sleep" in command else None
        with self.lock:
            session.holder = holder
        try:
            segments = shell.parse(command, env, holder.pid if holder else os.getpid())
        except shell.UnsupportedCommandError as exc:
            _kill(holder)
            self._fail(part, "tool.failed", str(exc))
            return str(exc), False
        if self._restart_due() and self._wait(session, TOOL_SECONDS_CAP):
            _kill(holder)
            return None
        output: list[str] = []
        directory = Path(session.directory)
        try:
            for segment in segments:
                if isinstance(segment, shell.Echo):
                    if segment.target is None:
                        output.append(segment.text + "\n")
                    else:
                        (directory / segment.target).write_text(segment.text + "\n")
                elif isinstance(segment, shell.Touch):
                    (directory / segment.path).touch()
                elif self._wait(session, min(segment.seconds, TOOL_SECONDS_CAP)):
                    return None
        finally:
            with self.lock:
                session.holder = None
            _kill(holder)
        if session.cancel.is_set():
            return None
        text = "".join(output)
        self._complete(part, text)
        return text, False

    def _restart_due(self) -> bool:
        """Under ``RESTARTS_MID_TURN``, the first shell tool of the store's life asks for a restart and then holds,
        so the restart ends this lifetime while the tool still runs."""
        with self.lock:
            if Lever.RESTARTS_MID_TURN not in self.levers or self._restarted_by_lever:
                return False
            self._restarted_by_lever = True
            self.save()
        self._restart()
        return True

    def _ask(self, session: Session, part: Json, command: str, message_id: str) -> str | None:
        ask = _Ask(
            {
                "id": records.new_id("per"),
                "sessionID": session.id,
                "action": "shell",
                "resources": [command],
                "save": [rules.save_pattern(command)],
                "source": {"type": "tool", "id": part["id"], "messageID": message_id},
            }
        )
        with self.lock:
            session.asks[str(ask.request["id"])] = ask
            _state(part)["status"] = "pending"
        self.publish("session.permission.asked", session, requestID=ask.request["id"])
        ask.answered.wait()
        with self.lock:
            session.asks.pop(str(ask.request["id"]), None)
            _state(part)["status"] = "running"
        if session.cancel.is_set() or ask.decision is None:
            return None
        return ask.decision

    def _read(self, session: Session, part: Json, path: str) -> tuple[str, bool]:
        if rules.evaluate(session.permissions, "read", path) == "deny":
            self._fail(part, "permission.rejected", "Permission denied: read")
            return "", True
        try:
            text = (Path(session.directory) / path).read_text()
        except OSError as exc:
            self._fail(part, "tool.failed", str(exc))
            return "", False
        self._complete(part, text, {"truncated": False})
        return text, False

    def _subagent(self, session: Session, part: Json, step: script.Subagent) -> tuple[str, bool] | None:
        child = self.create(
            {
                "id": records.new_id("ses"),
                "parentID": session.id,
                "location": {"directory": session.directory},
                "model": session.model,
                "title": step.instructions[:60],
                "agent": "general",
            }
        )
        with self.lock:
            _state(part)["metadata"] = {"sessionID": child.id, "status": "running", "truncated": False}
            _state(part)["input"] = {"agent": "general", "prompt": step.instructions, "background": step.background}
        self.prompt(child.id, step.instructions, None)
        if step.background:
            self._complete(
                part,
                f'<subagent sessionID="{child.id}" state="running">\n</subagent>',
                {"sessionID": child.id, "status": "running", "truncated": False},
            )
            return "", False
        while child.running:
            if self._wait(session, 0.1):
                return None
        if child.outcome != "succeeded":
            self._fail(part, "aborted", "Subagent interrupted")
            if self._wait(session, AFTER_CHILD_INTERRUPT_SECONDS):
                return None
            return "", False
        reply = _final_text(child)
        self._complete(
            part,
            f'<subagent sessionID="{child.id}" state="completed">\n{reply}\n</subagent>',
            {"sessionID": child.id, "status": "completed", "truncated": False},
        )
        return reply, False

    def _child_settled(self, child: Session) -> None:
        """A finished background child wakes its parent through the parent's inbox."""
        with self.lock:
            parent = self.sessions.get(child.parent_id or "")
            if parent is None or child.outcome != "succeeded":
                return
            spawned_in_background = any(
                isinstance(part, dict)
                and part.get("name") == "subagent"
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
                "text": _final_text(child),
                "description": child.title,
            }
            parent.inbox.append(entry)
            self._start(parent)
        self.publish("session.inbox.enqueued", parent, inboxID=entry["id"])

    def _deliver_inbox(self, session: Session) -> None:
        if self._wait(session, STEP_SECONDS):
            return
        with self.lock:
            if not session.inbox:
                return
            entry = session.inbox.pop(0)
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
        self._execute(session, script.Plan("", ()), "")

    def _compact(self, session: Session) -> None:
        if self._wait(session, STEP_SECONDS):
            self._idle(session, "interrupted")
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
        self._idle(session, "succeeded")


def _now() -> Json:
    return {"time": {"created": records.now_ms()}}


def _object(value: object) -> Json:
    return dict(value) if isinstance(value, dict) else {}


def _objects(value: object) -> list[Json]:
    return [dict(item) for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def _time(message: Json) -> Json:
    timing = message.setdefault("time", {})
    assert isinstance(timing, dict)
    return timing


def _state(part: Json) -> Json:
    state = part.setdefault("state", {})
    assert isinstance(state, dict)
    return state


def _content(message: object) -> list[Json]:
    content = message.get("content") if isinstance(message, dict) else None
    return content if isinstance(content, list) else []


def _input(step: script.ToolStep) -> Json:
    if isinstance(step, script.Shell):
        return {"command": step.command}
    if isinstance(step, script.Read):
        return {"filePath": step.path}
    return {"agent": "general", "prompt": step.instructions}


def _abort_assistant(message: Json, tool_message: str) -> None:
    for part in _content(message):
        if part.get("type") == "tool" and _state(part).get("status") in {"running", "pending"}:
            _state(part).update({"status": "error", "error": {"type": "aborted", "message": tool_message}})
    message.update({"error": {"type": "aborted", "message": "Step interrupted"}, "finish": "error"})
    _time(message).setdefault("completed", records.now_ms())


def _final_text(session: Session) -> str:
    assistants = [m for m in session.messages if m.get("type") == "assistant"]
    parts = _content(assistants[-1]) if assistants else []
    return "\n".join(str(p.get("text")) for p in parts if p.get("type") == "text")


def _kill(process: subprocess.Popen[bytes] | None) -> None:
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


__all__ = ["ConflictError", "Engine", "NotFoundError", "Session", "restart_in_background"]
