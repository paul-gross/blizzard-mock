"""``debug agent --tool`` and the "...permission" turn — the exact-denial-message probe."""

from __future__ import annotations

from ..levers import Lever

SESSION_ID = "ses_permission"
#: The one owning copy of the exact-denial sentence; ``domain.security`` imports it rather than restating it.
DENIAL = "The user has specified a rule which prevents you from using this specific tool call."


def debug_agent_tool_lines(levers: frozenset[Lever]) -> list[str]:
    """The stderr line(s) ``debug agent --tool`` prints; it always exits 1."""
    if Lever.IGNORE_CONFIG in levers:
        return ["The runner-owned configuration was ignored."]
    if Lever.PERMISSION_REQUEST_ONLY in levers:
        return ["The tool call requested permission but was not denied."]
    if Lever.PERMISSION_PROSE_ONLY in levers:
        return ["The model described a denied tool call without executing it."]
    lines = [DENIAL]
    if Lever.PERMISSION_DUPLICATE in levers:
        lines.append(DENIAL)
    return lines


def build_permission_events(levers: frozenset[Lever]) -> list[dict[str, object]]:
    """The JSONL event(s) a "...permission" turn prints."""
    sid = SESSION_ID
    if Lever.PERMISSION_PROSE_ONLY in levers:
        return [
            {
                "type": "step_start",
                "sessionID": sid,
                "part": {
                    "id": "prt_permission_start",
                    "sessionID": sid,
                    "messageID": "msg_permission",
                    "type": "step-start",
                },
            },
            {
                "type": "text",
                "sessionID": sid,
                "part": {
                    "id": "prt_permission_text",
                    "sessionID": sid,
                    "messageID": "msg_permission",
                    "type": "text",
                    "text": "The rule prevents you from using this specific tool call.",
                },
            },
        ]
    error = "unrelated tool failure" if Lever.PERMISSION_REQUEST_ONLY in levers else DENIAL
    if Lever.PERMISSION_OS_ERROR in levers:
        # Shared across every denial event, not just the last — matching the
        # retired ground-truth fake's behavior when both levers are armed together.
        error = "permission denied"
    part_ids = ["prt_permission"]
    if Lever.PERMISSION_DUPLICATE in levers:
        part_ids.append("prt_permission_duplicate")
    return [_denial_event(part_id, error) for part_id in part_ids]


def _denial_event(part_id: str, error: str) -> dict[str, object]:
    """One ``tool_use`` event carrying a denied ``printf permission-probe`` bash call."""
    return {
        "type": "tool_use",
        "sessionID": SESSION_ID,
        "part": {
            "id": part_id,
            "sessionID": SESSION_ID,
            "messageID": "msg_permission",
            "type": "tool",
            "callID": "call_permission",
            "tool": "bash",
            "state": {"status": "error", "input": {"command": "printf permission-probe"}, "error": error},
        },
    }


def permission_exit_code(levers: frozenset[Lever]) -> int:
    """The exit code a "...permission" turn returns."""
    return 9 if Lever.PERMISSION_NONZERO in levers else 0
