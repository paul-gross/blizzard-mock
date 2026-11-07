"""The engine-backed mock's turns: each turn's script runs in a child process (``turn``) in the session's directory
and environment, and the service builds the session's messages from what it writes, judging each tool call by the
session's rules first. The child is the turn's holder, so an interrupt ends it and every command it runs; a steer
ends it at its next tool call or step and runs as the turn's next script."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import threading
from contextlib import suppress
from pathlib import Path
from typing import IO, cast

from ...opencode2_core.domain import records
from ...opencode2_core.internal.sessions import Engine, Json, Session, final_text, kill
from ..domain import failures, fence, selftest

#: How long a script that reported its end is given to exit before it is ended.
EXIT_GRACE_SECONDS = 5.0
#: How long the shell call that asked for a ``RESTARTS_MID_TURN`` restart is held, so the restart lands while it runs.
RESTART_HOLD_SECONDS = 30.0
#: The most of a failed script's stderr its turn's error carries.
STDERR_TAIL_CHARS = 2000


class _Steered(Exception):
    def __init__(self, text: str) -> None:
        super().__init__(text)
        self.text = text


class _Declined(Exception):
    """A rejected permission request aborted the step: the turn ends there, with no idle."""


class _Interrupted(Exception):
    pass


class ScriptedTurns:
    """``argv0`` is the artifact itself, which every turn's child process runs as ``turn``."""

    def __init__(self, argv0: str) -> None:
        self._argv0 = argv0
        self._wakes: dict[str, str] = {}
        self._selftests: set[str] = set()
        self._lock = threading.Lock()

    def run(self, engine: Engine, session: Session, prompt: str) -> None:
        messages = session.messages
        resumed = not (messages and messages[-1].get("type") == "user")
        self._script(engine, session, self._selftest_answer(session, prompt), {"resumed": resumed})

    def _selftest_answer(self, session: Session, prompt: str) -> str:
        """``prompt``, or the selftest's script in place of its prose. A session is the selftest's from its first prose
        turn carrying the selftest's identity, which commits; a judgement or a resume after it may carry none."""
        script = selftest.answer(prompt, first=False)
        if script is None:
            return prompt
        with self._lock:
            if session.id in self._selftests:
                return script
            if not selftest.selftest_identity(session.environment or {}):
                return prompt
            self._selftests.add(session.id)
        return selftest.answer(prompt, first=True) or prompt

    def wake(self, engine: Engine, session: Session, child: Session) -> None:
        with self._lock:
            script = self._wakes.pop(child.id, None)
        if script:
            self._script(engine, session, script, {"wake": {"child": child.id, "text": final_text(child)}})
            return
        assistant = engine.open_step(session)
        tokens, cost = records.usage(session.effective_model(), "", "")
        engine.close_step(session, assistant, tokens=tokens, cost=cost, finish="stop")
        engine.idle(session, "succeeded")

    def _script(self, engine: Engine, session: Session, script: str, extra: Json) -> None:
        env = dict(os.environ) if session.environment is None else dict(session.environment)
        refusal = fence.refusal(Path(session.directory), env)
        if refusal is not None:
            engine.idle(session, "failed", failures.unfenced(refusal))
            return
        turn = _Turn(engine, session, script, self)
        try:
            turn.run(self._argv0, env, {"session": session.id, "model": session.effective_model(), **extra})
        except _Steered as steered:
            engine.user_message(session, steered.text)
            self._script(engine, session, steered.text, {"resumed": False})

    def remember_wake(self, child_id: str, script: str) -> None:
        with self._lock:
            self._wakes[child_id] = script


class _Turn:
    """One script's run: its child process, the step being written, and that step's scripted usage."""

    def __init__(self, engine: Engine, session: Session, script: str, turns: ScriptedTurns) -> None:
        self.engine = engine
        self.session = session
        self.script = script
        self.turns = turns
        self.assistant: Json = {}
        self.usage: tuple[Json, float] | None = None
        self.texts: list[str] = []
        self.parts: dict[str, Json] = {}
        self.process: subprocess.Popen[bytes] | None = None
        self.closed = False

    def run(self, argv0: str, env: dict[str, str], header: Json) -> None:
        engine, session = self.engine, self.session
        self.assistant = engine.open_step(session)
        with tempfile.TemporaryFile() as stderr:
            process = subprocess.Popen(
                [sys.executable, argv0, "turn"],
                cwd=session.directory,
                env=env,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=stderr,
                start_new_session=True,
            )
            self.process = process
            engine.hold(session, process)
            assert process.stdin is not None and process.stdout is not None
            try:
                ended = self._converse(process.stdin, process.stdout, {**header, "script": self.script})
            except _Interrupted:
                self._release()
                engine.interrupted(session, self.assistant)
                return
            except _Declined:
                self._release()
                return
            except _Steered:
                self._close("stop")
                self._release()
                raise
            if ended == "end":
                with suppress(subprocess.TimeoutExpired):
                    process.wait(EXIT_GRACE_SECONDS)
            self._release()
            if session.cancel.is_set():
                engine.interrupted(session, self.assistant)
            elif ended == "end":
                self._close("stop")
                engine.idle(session, "succeeded")
            elif ended != "failed":
                stderr.seek(0)
                tail = stderr.read().decode(errors="replace")[-STDERR_TAIL_CHARS:].strip()
                message = tail.splitlines()[-1] if tail else f"the script exited {process.returncode} unfinished"
                engine.fail_turn(session, self.assistant, failures.script_failed(message))

    def _release(self) -> None:
        self.engine.hold(self.session, None)
        kill(self.process)

    def _close(self, finish: str) -> None:
        """End the step being written, once: a steer taken at a step boundary finds it already ended."""
        if self.closed:
            return
        tokens, cost = self.usage or records.usage(self.session.effective_model(), self.script, "".join(self.texts))
        self.engine.close_step(self.session, self.assistant, tokens=tokens, cost=cost, finish=finish)
        self.closed = True

    def _converse(self, replies: IO[bytes], requests: IO[bytes], header: Json) -> str | None:
        """Answer the child's requests until it ends; ``end``, ``failed``, or ``None`` when the pipe closed first."""
        if not _write(replies, header):
            return None
        for line in requests:
            try:
                decoded: object = json.loads(line)
            except ValueError:
                continue
            if not isinstance(decoded, dict):
                continue
            request = cast(Json, decoded)
            op = str(request.get("op"))
            if op == "end":
                return "end"
            if op == "fail":
                error = request.get("error")
                self.engine.fail_turn(
                    self.session, self.assistant, cast(Json, error) if isinstance(error, dict) else {}
                )
                return "failed"
            reply = self._answer(op, request)
            if self.session.cancel.is_set():
                raise _Interrupted
            if reply is not None and not _write(replies, reply):
                return None
        return None

    def _answer(self, op: str, request: Json) -> Json | None:
        engine, session = self.engine, self.session
        if op == "text":
            content = str(request.get("text", ""))
            self.texts.append(content)
            engine.add_part(session, self.assistant, {"type": "text", "text": content})
            engine.text_ended(session, self.assistant, content)
        elif op == "reasoning":
            now = records.now_ms()
            part: Json = {
                "type": "reasoning",
                "text": str(request.get("text", "")),
                "time": {"created": now, "completed": now},
            }
            engine.add_part(session, self.assistant, part)
        elif op == "usage":
            tokens, cost = request.get("tokens"), request.get("cost")
            self.usage = (
                tokens if isinstance(tokens, dict) else {},
                float(cost) if isinstance(cost, int | float) else 0,
            )
        elif op == "step":
            self._close("tool-calls")
            self._steer()
            self.assistant, self.usage, self.texts = engine.open_step(session), None, []
            self.closed = False
        elif op == "tool":
            return self._tool(request)
        elif op in {"tool.done", "tool.failed"}:
            called = self.parts.pop(str(request.get("call")), None)
            if called is not None:
                if op == "tool.done":
                    metadata = request.get("metadata")
                    output = str(request.get("output", ""))
                    engine.complete_tool(
                        session, called, output, cast(Json, metadata) if isinstance(metadata, dict) else None
                    )
                else:
                    engine.fail_tool(session, called, str(request.get("kind")), str(request.get("message")))
            self._steer()
        elif op == "subagent":
            return self._subagent(request)
        elif op == "form":
            fields = request.get("fields")
            raised = engine.await_form(
                session,
                str(request.get("title", "")),
                [cast(Json, f) for f in cast("list[object]", fields) if isinstance(f, dict)]
                if isinstance(fields, list)
                else [],
            )
            if raised is None:
                raise _Interrupted
            return {"form": raised}
        return None

    def _steer(self) -> None:
        steer = self.engine.take_steer(self.session)
        if steer is not None:
            raise _Steered(steer)

    def _tool(self, request: Json) -> Json:
        engine, session = self.engine, self.session
        name, resource = str(request.get("name")), str(request.get("resource", "*"))
        tool_input = request.get("input")
        part = engine.open_tool(
            session, self.assistant, name, cast(Json, tool_input) if isinstance(tool_input, dict) else {}
        )
        if part is None:
            return {"verdict": "withheld"}
        verdict = engine.judge(session, self.assistant, part, name, resource, ask=request.get("ask") is True)
        if verdict is None:
            raise _Interrupted
        if verdict == "declined":
            raise _Declined
        if verdict == "deny":
            self._steer()
            return {"verdict": "deny"}
        self.parts[str(part["id"])] = part
        if name == "shell" and engine.restart_due():
            engine.wait(session, RESTART_HOLD_SECONDS)
        return {"verdict": "allow", "call": part["id"]}

    def _subagent(self, request: Json) -> Json:
        engine, session = self.engine, self.session
        script = str(request.get("script", ""))
        background = request.get("background") is True
        description = str(request.get("description") or "")
        tool_input: Json = {"agent": "general", "prompt": script, "background": background}
        if description:
            tool_input["description"] = description
        part = engine.open_tool(session, self.assistant, "subagent", tool_input)
        if part is None:
            return {"verdict": "withheld"}
        child = engine.spawn_child(session, part, script, title=description or script[:60], background=background)
        wake = request.get("wake")
        if background and isinstance(wake, str) and wake:
            self.turns.remember_wake(child.id, wake)
        reply = engine.settle_child(session, part, child, background=background)
        if reply is None:
            raise _Interrupted
        state = part.get("state")
        status = cast(Json, state).get("status") if isinstance(state, dict) else None
        self._steer()
        return {"verdict": "allow", "output": reply, "session": child.id, "status": status}


def _write(pipe: IO[bytes], payload: Json) -> bool:
    try:
        pipe.write(json.dumps(payload).encode() + b"\n")
        pipe.flush()
    except (BrokenPipeError, OSError):
        return False
    return True


__all__ = ["EXIT_GRACE_SECONDS", "ScriptedTurns"]
