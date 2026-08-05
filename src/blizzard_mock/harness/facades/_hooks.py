"""``--settings`` hook execution — only the ``claude_code`` facade uses it.

Runs hook commands as real subprocesses; implements
:class:`~blizzard_mock.harness.engine.IHookRunner`. Degrades rather than fails:
a missing file, bad JSON, or a wedged command all leave the turn intact.
"""

from __future__ import annotations

import json
import subprocess
from collections.abc import Mapping, Sequence
from pathlib import Path

from blizzard_mock.harness.engine import IHookRunner, RunResult
from blizzard_mock.harness.internal.logging import get_logger

_log = get_logger(__name__)

#: Per-hook wall-clock budget, in seconds — real Claude Code's own default
#: (pinned by tests/test_harness_hooks.py).
DEFAULT_HOOK_TIMEOUT_SECONDS = 60.0

#: The two hook events wired here. The payload's ``hook_event_name`` discriminates,
#: so an event we are not wiring yet drops in without a payload redesign.
POST_TOOL_USE = "PostToolUse"
SESSION_END = "SessionEnd"

#: ``SessionEnd``'s ``reason``: only ``other`` describes a headless ``-p``
#: process exiting; the run's outcome is not used to derive it.
SESSION_END_REASON = "other"


def _read_document(path: Path) -> Mapping[str, object]:
    """The settings document at ``path``, or an empty mapping when unusable."""
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        _log.warning("hook settings unreadable", path=str(path), error=repr(exc))
        return {}
    try:
        document = json.loads(raw)
    except json.JSONDecodeError as exc:
        _log.warning("hook settings are not valid JSON", path=str(path), error=str(exc))
        return {}
    return document if isinstance(document, Mapping) else {}


def _commands(document: Mapping[str, object], event: str) -> list[str]:
    """The command strings ``document`` declares for one hook event, in order.

    Reads ``hooks.<Event>[].hooks[]`` entries carrying ``type: "command"``.
    Anything malformed is skipped rather than fatal.
    """
    hooks = document.get("hooks")
    if not isinstance(hooks, Mapping):
        return []
    matchers = hooks.get(event)
    if not isinstance(matchers, Sequence) or isinstance(matchers, str):
        return []
    commands: list[str] = []
    for matcher in matchers:
        if not isinstance(matcher, Mapping):
            continue
        entries = matcher.get("hooks")
        if not isinstance(entries, Sequence) or isinstance(entries, str):
            continue
        for entry in entries:
            if not isinstance(entry, Mapping) or entry.get("type") != "command":
                continue
            command = entry.get("command")
            if isinstance(command, str) and command.strip():
                commands.append(command)
    return commands


class SettingsHookRunner:
    """Runs the hook commands one ``--settings`` document declares.

    One instance per turn; ``cwd``/env are fixed at construction and inherited.
    """

    def __init__(
        self,
        settings_path: Path,
        *,
        cwd: Path,
        env: Mapping[str, str],
        session_id: str,
        transcript_path: Path | None = None,
        timeout: float = DEFAULT_HOOK_TIMEOUT_SECONDS,
    ) -> None:
        document = _read_document(settings_path)
        self._post_tool_use = _commands(document, POST_TOOL_USE)
        self._session_end = _commands(document, SESSION_END)
        self._cwd = cwd
        self._env = dict(env)
        self._session_id = session_id
        self._transcript_path = transcript_path
        self._timeout = timeout

    @property
    def has_commands(self) -> bool:
        """Whether this document declared anything to run at either event."""
        return bool(self._post_tool_use or self._session_end)

    def on_tool_use(self, name: str, tool_input: Mapping[str, object], tool_output: str) -> None:
        payload = self._payload(POST_TOOL_USE)
        payload["tool_name"] = name
        payload["tool_input"] = dict(tool_input)
        payload["tool_response"] = tool_output
        self._fire(POST_TOOL_USE, self._post_tool_use, payload)

    def on_session_end(self, result: RunResult) -> None:
        payload = self._payload(SESSION_END, session_id=result.session_id)
        payload["reason"] = SESSION_END_REASON
        self._fire(SESSION_END, self._session_end, payload)

    def _payload(self, event: str, *, session_id: str | None = None) -> dict[str, object]:
        """The Claude-Code-shaped JSON body this event's commands read on stdin."""
        payload: dict[str, object] = {
            "hook_event_name": event,
            "session_id": session_id if session_id is not None else self._session_id,
            "cwd": str(self._cwd),
        }
        if self._transcript_path is not None:
            payload["transcript_path"] = str(self._transcript_path)
        return payload

    def _fire(self, event: str, commands: Sequence[str], payload: Mapping[str, object]) -> None:
        if not commands:
            return
        body = json.dumps(payload)
        for command in commands:
            self._run(event, command, body)

    def _run(self, event: str, command: str, body: str) -> None:
        """Run one hook command to completion, or log why it did not.

        Output is captured, never inherited, so a chatty hook cannot interleave
        with the wire envelope on this process's stdout.
        """
        try:
            completed = subprocess.run(
                command,
                shell=True,
                cwd=self._cwd,
                env=self._env,
                input=body,
                text=True,
                capture_output=True,
                timeout=self._timeout,
            )
        except subprocess.TimeoutExpired:
            _log.warning("hook command timed out", hook_event=event, command=command, timeout=self._timeout)
            return
        except (OSError, subprocess.SubprocessError) as exc:
            _log.warning("hook command could not run", hook_event=event, command=command, error=repr(exc))
            return
        if completed.returncode != 0:
            _log.warning(
                "hook command exited nonzero",
                hook_event=event,
                command=command,
                returncode=completed.returncode,
                stderr=completed.stderr.strip()[-500:],
            )


def build_hook_runner(
    settings_path: str | None,
    *,
    cwd: Path,
    env: Mapping[str, str],
    session_id: str | None,
    transcript_path: Path | None = None,
    timeout: float = DEFAULT_HOOK_TIMEOUT_SECONDS,
) -> SettingsHookRunner | None:
    """The hook runner for this run, or ``None`` when there is nothing to fire.

    ``None`` covers every degradation: no ``--settings``, a missing path,
    unparseable JSON, or no commands under either wired event.
    """
    if not settings_path:
        return None
    runner = SettingsHookRunner(
        Path(settings_path),
        cwd=cwd,
        env=env,
        session_id=session_id or "",
        transcript_path=transcript_path,
        timeout=timeout,
    )
    return runner if runner.has_commands else None


# Typecheck-time Protocol conformance sentinel, matching the pattern
# ``blizzard-context:/exemplars/python/repo_pattern.py`` documents.
def _conforms_hook_runner(x: SettingsHookRunner) -> IHookRunner:
    return x
