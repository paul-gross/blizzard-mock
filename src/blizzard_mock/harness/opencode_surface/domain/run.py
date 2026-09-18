"""The default fresh/resume run turn — the fallback when no other shaped probe matched."""

from __future__ import annotations

from ..levers import Lever

#: The session id every default turn answers with, absent ``--session``/the wrong-session lever.
DEFAULT_SESSION_ID = "ses_fake"

#: Substring-matched against the last arg; "permission" is last since it's the shortest marker and could match others.
_PROBE_MARKERS = (
    ("process-control turn", "process_control"),
    ("configuration turn", "configuration"),
    ("security proof", "security"),
    ("permission", "permission"),
)


def classify_probe(args: list[str]) -> str | None:
    """Which shaped probe this fresh/resume turn's script names as its last arg, else
    ``None`` for the plain default run turn (the fallback when no probe matched)."""
    last = args[-1] if args else ""
    for marker, probe in _PROBE_MARKERS:
        if marker in last:
            return probe
    return None


def resolve_session_id(args: list[str], levers: frozenset[Lever]) -> str:
    """The session id this turn answers with."""
    sid = args[args.index("--session") + 1] if "--session" in args else DEFAULT_SESSION_ID
    if Lever.TAKEOVER_WRONG_SESSION in levers and "--dir" in args:
        sid = "ses_wrong"
    return sid


def needs_compatibility_commit(args: list[str]) -> bool:
    """A fresh (no ``--session``) turn proves real-git compatibility by committing."""
    return "--session" not in args


def is_sleep5_turn(args: list[str]) -> bool:
    """Whether this turn's script asks for the observable "still running" window."""
    return "sleep 5" in (args[-1] if args else "")


def provider_refusal_event(sid: str) -> dict:
    """The provider-error event ``PROVIDER_REFUSAL`` emits instead of a normal turn."""
    return {
        "type": "error",
        "sessionID": sid,
        "error": {"name": "APIError", "data": {"message": "The usage limit has been reached", "statusCode": 429}},
    }


def sleep5_step_start(sid: str) -> dict:
    """The step-start event printed before the observable one-second sleep."""
    return {
        "type": "step_start",
        "sessionID": sid,
        "part": {"id": "prt_start", "sessionID": sid, "messageID": "msg_assistant", "type": "step-start"},
    }


def default_turn_events(sid: str) -> list[dict]:
    """The step-start/text/step-finish trio every ordinary turn ends with."""
    return [
        {
            "type": "step_start",
            "sessionID": sid,
            "part": {"id": "prt_start", "sessionID": sid, "messageID": "msg_assistant", "type": "step-start"},
        },
        {
            "type": "text",
            "sessionID": sid,
            "part": {
                "id": "prt_text",
                "sessionID": sid,
                "messageID": "msg_assistant",
                "type": "text",
                "text": "Done. <Choice>pass</Choice>",
            },
        },
        {
            "type": "step_finish",
            "sessionID": sid,
            "part": {
                "id": "prt_finish",
                "sessionID": sid,
                "messageID": "msg_assistant",
                "type": "step-finish",
                "reason": "stop",
                "cost": 0.01,
                "tokens": {"input": 2, "output": 3, "reasoning": 0, "cache": {"read": 0, "write": 0}},
            },
        },
    ]


def fresh_nonzero_exit(args: list[str], levers: frozenset[Lever]) -> int | None:
    """``FRESH_NONZERO``'s override exit code for a non-resumed turn, else ``None``."""
    if Lever.FRESH_NONZERO in levers and "--session" not in args:
        return 7
    return None
