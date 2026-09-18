"""``export <session-id>`` — the transcript document a diagnostic parses."""

from __future__ import annotations

from ..levers import Lever
from .state import is_running

#: Hardcoded to the ground-truth fake's own literal — deliberately independent of
#: whatever ``--version`` this artifact was emitted with.
_INFO_VERSION = "1.18.25"


def build(sid: str, state: dict, levers: frozenset[Lever], cwd: str) -> dict:
    """The export document for session ``sid``, given its persisted state."""
    static_replay = Lever.STATIC_REPLAY in levers
    running = is_running(state, static_replay=static_replay)
    compaction_generation = int(state.get("compaction_generation", 0))
    phase = state.get("phase")
    command = "sleep 30" if phase == "process_control" else "sleep 5"
    if running:
        tool_state = {"status": "running", "input": {"command": command}, "title": "Long-running command"}
    else:
        tool_state = {
            "status": "completed",
            "input": {"command": "sleep 5"},
            "output": "slept",
            "title": "Long-running command",
            "metadata": {},
        }
    directory = "/wrong-directory" if Lever.TAKEOVER_WRONG_DIRECTORY in levers else cwd

    export = {
        "info": {"id": sid, "version": _INFO_VERSION, "directory": directory, "title": "proof"},
        "messages": [
            {
                "info": {"id": "msg_user", "sessionID": sid, "role": "user"},
                "parts": [
                    {"id": "prt_user", "sessionID": sid, "messageID": "msg_user", "type": "text", "text": "proof"}
                ],
            },
            {
                "info": {
                    "id": "msg_assistant",
                    "sessionID": sid,
                    "role": "assistant",
                    "providerID": "openai",
                    "modelID": "gpt-5.6-luna",
                    "variant": "max",
                    "tokens": {"input": 2, "output": 3, "reasoning": 0, "cache": {"read": 0, "write": 0}},
                    "cost": 0.01,
                },
                "parts": [
                    {"id": "prt_start", "sessionID": sid, "messageID": "msg_assistant", "type": "step-start"},
                    {
                        "id": "prt_tool",
                        "sessionID": sid,
                        "messageID": "msg_assistant",
                        "type": "tool",
                        "callID": "call_sleep",
                        "tool": "bash",
                        "state": tool_state,
                    },
                ],
            },
        ],
    }
    if not running:
        export["messages"] = [export["messages"][1]]
        export["messages"].append(
            {
                "info": {"id": "msg_compaction_user", "sessionID": sid, "role": "user"},
                "parts": [
                    {
                        "id": f"prt_compaction_{compaction_generation}",
                        "sessionID": sid,
                        "messageID": "msg_compaction_user",
                        "type": "compaction",
                    }
                ],
            }
        )
        export["messages"].append(
            {
                "info": {
                    "id": "msg_after_compaction",
                    "sessionID": sid,
                    "role": "assistant",
                    "providerID": "openai",
                    "modelID": "gpt-5.6-luna",
                    "variant": "max",
                },
                "parts": [
                    {
                        "id": "prt_after_compaction",
                        "sessionID": sid,
                        "messageID": "msg_after_compaction",
                        "type": "text",
                        "text": "After compaction.",
                    }
                ],
            }
        )
        export["messages"][0]["parts"].extend(
            [
                {
                    "id": "prt_text",
                    "sessionID": sid,
                    "messageID": "msg_assistant",
                    "type": "text",
                    "text": "Done. <Choice>pass</Choice>",
                },
                {
                    "id": "prt_finish",
                    "sessionID": sid,
                    "messageID": "msg_assistant",
                    "type": "step-finish",
                    "reason": "stop",
                    "cost": 0.01,
                    "tokens": {"input": 2, "output": 3, "reasoning": 0, "cache": {"read": 0, "write": 0}},
                },
            ]
        )
    takeover_prompt = state.get("takeover_prompt")
    if isinstance(takeover_prompt, str):
        export["messages"].append(
            {
                "info": {"id": "msg_takeover_user", "sessionID": sid, "role": "user"},
                "parts": [
                    {
                        "id": "prt_takeover_user",
                        "sessionID": sid,
                        "messageID": "msg_takeover_user",
                        "type": "text",
                        "text": takeover_prompt,
                    }
                ],
            }
        )
    return export
