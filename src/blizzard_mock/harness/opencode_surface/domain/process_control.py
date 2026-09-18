"""The "...process-control turn" probe — a step-start event, then the caller sleeps."""

from __future__ import annotations

SESSION_ID = "ses_process_control"


def build_event() -> dict:
    """The one event a process-control turn prints before it sleeps."""
    return {
        "type": "step_start",
        "sessionID": SESSION_ID,
        "part": {
            "id": "prt_process_control_start",
            "sessionID": SESSION_ID,
            "messageID": "msg_process_control",
            "type": "step-start",
        },
    }
