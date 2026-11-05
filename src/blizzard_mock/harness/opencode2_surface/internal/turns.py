"""The CLI-surface artifact's turns: a prompt's plan (``domain.script``) answered with fixed behavior, never executed.

Shell calls run the fake shell's grammar against the session's environment and directory, a ``sleep`` holds a real
stand-in process so the tool's pid can be seen and stopped, rules are enforced as OpenCode 2.1.4 enforces them, and a
subagent runs as a child session."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

from ...opencode2_core.domain import records
from ...opencode2_core.internal.sessions import STEP_SECONDS, Engine, Json, Session, kill
from ..domain import script, shell

#: The longest a fake ``sleep`` holds its tool; long enough to be seen running, short enough for a test tier.
TOOL_SECONDS_CAP = 6.0


class PlannedTurns:
    """Each turn in one assistant step: its plan's tool calls, then the reply the prompt asks for."""

    def run(self, engine: Engine, session: Session, prompt: str) -> None:
        self._execute(engine, session, script.plan(prompt), prompt)

    def wake(self, engine: Engine, session: Session, child: Session) -> None:
        del child
        self._execute(engine, session, script.Plan("", ()), "")

    def _execute(self, engine: Engine, session: Session, plan: script.Plan, prompt: str) -> None:
        assistant = engine.open_step(session)
        outputs: list[str] = []
        refused = False
        for step in plan.steps:
            if engine.wait(session, STEP_SECONDS):
                engine.interrupted(session, assistant)
                return
            outcome = self._tool(engine, session, assistant, step)
            if outcome is None:
                engine.interrupted(session, assistant)
                return
            output, was_refused = outcome
            outputs.append(output)
            refused = refused or was_refused
            steer = engine.take_steer(session)
            if steer is not None:
                self._close(engine, session, assistant, "", prompt)
                engine.user_message(session, steer)
                self._execute(engine, session, script.plan(steer), steer)
                return
        if engine.wait(session, STEP_SECONDS):
            engine.interrupted(session, assistant)
            return
        reply = plan.reply(outputs, refused=refused)
        self._close(engine, session, assistant, reply, prompt)
        engine.idle(session, "succeeded")

    def _close(self, engine: Engine, session: Session, assistant: Json, reply: str, prompt: str) -> None:
        tokens, cost = records.usage(session.effective_model(), prompt, reply)
        engine.close_step(session, assistant, tokens=tokens, cost=cost, finish="stop", text=reply)

    def _tool(
        self, engine: Engine, session: Session, assistant: Json, step: script.ToolStep
    ) -> tuple[str, bool] | None:
        """Run one tool call; ``(output, refused)``, or ``None`` when the session was interrupted during it."""
        part = engine.open_tool(session, assistant, _name(step), _input(step))
        if part is None:
            return "", True
        if isinstance(step, script.Shell):
            return self._shell(engine, session, assistant, part, step.command)
        if isinstance(step, script.Read):
            return self._read(engine, session, assistant, part, step.path)
        if isinstance(step, script.Skill):
            return self._skill(engine, session, assistant, part, step.name)
        child = engine.spawn_child(
            session, part, step.instructions, title=step.instructions[:60], background=step.background
        )
        reply = engine.settle_child(session, part, child, background=step.background)
        return None if reply is None else (reply, False)

    def _shell(
        self, engine: Engine, session: Session, assistant: Json, part: Json, command: str
    ) -> tuple[str, bool] | None:
        verdict = engine.judge(session, assistant, part, "shell", command)
        if verdict is None:
            return None
        if verdict == "deny":
            return "", True
        env = dict(os.environ) if session.environment is None else dict(session.environment)
        holder = subprocess.Popen(["sleep", "3600"], start_new_session=True) if "sleep" in command else None
        engine.hold(session, holder)
        try:
            segments = shell.parse(command, env, holder.pid if holder else os.getpid())
        except shell.UnsupportedCommandError as exc:
            engine.hold(session, None)
            kill(holder)
            engine.fail_tool(session, part, "tool.failed", str(exc))
            return str(exc), False
        if engine.restart_due() and engine.wait(session, TOOL_SECONDS_CAP):
            kill(holder)
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
                elif engine.wait(session, min(segment.seconds, TOOL_SECONDS_CAP)):
                    return None
        finally:
            engine.hold(session, None)
            kill(holder)
        if session.cancel.is_set():
            return None
        text = "".join(output)
        engine.complete_tool(session, part, text)
        return text, False

    def _read(
        self, engine: Engine, session: Session, assistant: Json, part: Json, path: str
    ) -> tuple[str, bool] | None:
        verdict = engine.judge(session, assistant, part, "read", path)
        if verdict is None:
            return None
        if verdict == "deny":
            return "", True
        try:
            text = (Path(session.directory) / path).read_text()
        except OSError as exc:
            engine.fail_tool(session, part, "tool.failed", str(exc))
            return "", False
        engine.complete_tool(session, part, text, {"truncated": False})
        return text, False

    def _skill(
        self, engine: Engine, session: Session, assistant: Json, part: Json, name: str
    ) -> tuple[str, bool] | None:
        """A skill loads from the session directory's ``.opencode/skills/<name>/SKILL.md``, as 2.1.4 finds one."""
        verdict = engine.judge(session, assistant, part, "skill", name)
        if verdict is None:
            return None
        if verdict == "deny":
            return "", True
        directory = Path(session.directory) / ".opencode" / "skills" / name
        try:
            body = (directory / "SKILL.md").read_text()
        except OSError as exc:
            engine.fail_tool(session, part, "tool.failed", str(exc))
            return "", False
        text = f'<skill_content name="{name}">\n{body}</skill_content>'
        engine.complete_tool(session, part, text, {"name": name, "directory": str(directory), "truncated": False})
        return text, False


def _name(step: script.ToolStep) -> str:
    if isinstance(step, script.Shell):
        return "shell"
    if isinstance(step, script.Read):
        return "read"
    return "skill" if isinstance(step, script.Skill) else "subagent"


def _input(step: script.ToolStep) -> Json:
    if isinstance(step, script.Shell):
        return {"command": step.command}
    if isinstance(step, script.Read):
        return {"path": step.path}
    if isinstance(step, script.Skill):
        return {"id": step.name}
    return {"agent": "general", "prompt": step.instructions}


__all__ = ["TOOL_SECONDS_CAP", "PlannedTurns"]
