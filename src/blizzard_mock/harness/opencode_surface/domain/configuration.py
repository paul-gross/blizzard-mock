"""``debug config --pure`` and the "...configuration turn" probe.

File resolution (``OPENCODE_CONFIG``, project ``opencode.json``, the XDG
config file) is I/O and lives in ``internal.configuration``; this module only
decides, given an already-resolved config document, whether the probed
command is denied and what to render.
"""

from __future__ import annotations

import json

from ..levers import Lever

SESSION_ID = "ses_config"
#: The exact denial prose — used both by the CONFIGURATION_PROSE_ONLY shortcut
#: and as the ``tool_use`` error text on an ordinary denial.
PROSE_DENIAL = "The configured permission rule prevented this specific tool call."


def debug_config_pure(levers: frozenset[Lever], config_content: str) -> dict:
    """The effective-config document ``debug config --pure`` echoes back."""
    if Lever.IGNORE_CONFIG in levers:
        return {"model": "competing/ignored-model"}
    try:
        effective = json.loads(config_content or "{}")
    except json.JSONDecodeError:
        effective = {}
    if Lever.DROP_CONFIG_SHELL in levers:
        effective.pop("shell", None)
    if Lever.DROP_CONFIG_COMPACTION in levers:
        effective.pop("compaction", None)
    return effective


def decide(levers: frozenset[Lever], config: dict) -> bool:
    """Whether the configuration turn's probed bash command is denied."""
    if Lever.IGNORE_CONFIG in levers:
        return False
    try:
        bash_permission = config["permission"]["bash"]
        return bash_permission == "deny" or (
            isinstance(bash_permission, dict) and bash_permission["printf config-isolation-probe"] == "deny"
        )
    except KeyError:
        return False


def build_event(levers: frozenset[Lever], denied: bool) -> dict:
    """The ``tool_use`` event the configuration turn prints."""
    state: dict = {"status": "error" if denied else "completed", "input": {"command": "printf config-isolation-probe"}}
    if denied:
        state["error"] = "permission denied" if Lever.CONFIGURATION_OS_ERROR in levers else PROSE_DENIAL
    else:
        state["output"] = "config-isolation-probe"
    return {
        "type": "tool_use",
        "sessionID": SESSION_ID,
        "part": {
            "id": "prt_config",
            "sessionID": SESSION_ID,
            "messageID": "msg_config",
            "type": "tool",
            "callID": "call_config",
            "tool": "bash",
            "state": state,
        },
    }
