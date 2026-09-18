"""The "...security proof" turn — does the mock refuse the embedded shell command, or run it?"""

from __future__ import annotations

from ..levers import Lever

SESSION_ID = "ses_security"
DENIAL = "The user has specified a rule which prevents you from using this specific tool call."


def build_event(levers: frozenset[Lever], command: str) -> tuple[dict, bool]:
    """The ``tool_use`` event to print, and whether the caller must actually run ``command``."""
    execute = Lever.SECURITY_COMMAND_EXECUTES in levers
    if execute:
        state = {"status": "completed", "input": {"command": command}, "output": "executed"}
    else:
        state = {"status": "error", "input": {"command": command}, "error": DENIAL}
    event = {
        "type": "tool_use",
        "sessionID": SESSION_ID,
        "part": {
            "id": "prt_security",
            "sessionID": SESSION_ID,
            "messageID": "msg_security",
            "type": "tool",
            "callID": "call_security",
            "tool": "bash",
            "state": state,
        },
    }
    return event, execute
