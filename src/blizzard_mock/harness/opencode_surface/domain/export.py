"""``export <session-id>`` — the transcript document a diagnostic parses."""

from __future__ import annotations

from ..levers import Lever
from .state import is_running

#: Independent of whatever ``--version`` this artifact was emitted with.
_INFO_VERSION = "1.18.25"


def _user_message(sid: str) -> dict:
    """The initial "proof" user turn — present only while the tool call is still running."""
    return {
        "info": {"id": "msg_user", "sessionID": sid, "role": "user"},
        "parts": [{"id": "prt_user", "sessionID": sid, "messageID": "msg_user", "type": "text", "text": "proof"}],
    }


def _assistant_message(sid: str, tool_state: dict, *, running: bool) -> dict:
    """The assistant turn that ran the long-running tool call — carries the trailing
    text/step-finish parts once it has actually finished."""
    parts = [
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
    ]
    if not running:
        parts.extend(
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
    return {
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
        "parts": parts,
    }


def _compaction_user_message(sid: str, compaction_generation: int) -> dict:
    """The synthetic user turn a compaction inserts once the tool call has finished."""
    return {
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


def _after_compaction_message(sid: str) -> dict:
    """The assistant turn that follows a compaction."""
    return {
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


def _takeover_message(sid: str, takeover_prompt: str) -> dict:
    """The trailing user turn recording the operator's takeover line, when one arrived."""
    return {
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


def build(sid: str, state: dict, levers: frozenset[Lever], cwd: str) -> dict:
    """The export document for session ``sid``, given its persisted state. While the tool call
    is running, the transcript is the plain user+assistant pair; once finished, the user turn is
    replaced by a compaction sequence and the assistant message survives with its completed
    state."""
    static_replay = Lever.STATIC_REPLAY in levers
    running = is_running(state, static_replay=static_replay)
    phase = state.get("phase")
    command = "sleep 30" if phase == "process_control" else "sleep 5"

    if running:
        tool_state = {"status": "running", "input": {"command": command}, "title": "Long-running command"}
        messages = [_user_message(sid), _assistant_message(sid, tool_state, running=True)]
    else:
        tool_state = {
            "status": "completed",
            "input": {"command": "sleep 5"},
            "output": "slept",
            "title": "Long-running command",
            "metadata": {},
        }
        compaction_generation = int(state.get("compaction_generation", 0))
        messages = [
            _assistant_message(sid, tool_state, running=False),
            _compaction_user_message(sid, compaction_generation),
            _after_compaction_message(sid),
        ]

    takeover_prompt = state.get("takeover_prompt")
    if isinstance(takeover_prompt, str):
        messages.append(_takeover_message(sid, takeover_prompt))

    directory = "/wrong-directory" if Lever.TAKEOVER_WRONG_DIRECTORY in levers else cwd
    return {
        "info": {"id": sid, "version": _INFO_VERSION, "directory": directory, "title": "proof"},
        "messages": messages,
    }
