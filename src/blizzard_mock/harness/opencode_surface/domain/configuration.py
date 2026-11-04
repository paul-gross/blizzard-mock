"""``debug config --pure`` and the "...configuration turn" probe.

Config file resolution is I/O and lives in ``internal.configuration``; this
module only decides, given an already-resolved config document, whether the
probed command is denied and what to render."""

from __future__ import annotations

from typing import cast

from ..levers import Lever

SESSION_ID = "ses_config"
#: The exact denial prose, prose-only or as the ``tool_use`` error text.
PROSE_DENIAL = "The configured permission rule prevented this specific tool call."


def debug_config_pure(levers: frozenset[Lever], config: dict[str, object]) -> dict[str, object]:
    """The effective-config document ``debug config --pure`` echoes back, given the same
    resolved config the "...configuration turn" probe uses (``internal.configuration.resolve_config``)."""
    if Lever.IGNORE_CONFIG in levers:
        return {"model": "competing/ignored-model"}
    effective = dict(config)
    if Lever.DROP_CONFIG_SHELL in levers:
        effective.pop("shell", None)
    if Lever.DROP_CONFIG_COMPACTION in levers:
        effective.pop("compaction", None)
    return effective


def decide(levers: frozenset[Lever], config: dict[str, object]) -> bool:
    """Whether the configuration turn's probed bash command is denied."""
    if Lever.IGNORE_CONFIG in levers:
        return False
    permission = config.get("permission")
    if not isinstance(permission, dict):
        return False
    bash_permission: object = cast("dict[str, object]", permission).get("bash")
    if bash_permission == "deny":
        return True
    if isinstance(bash_permission, dict):
        return cast("dict[str, object]", bash_permission).get("printf config-isolation-probe") == "deny"
    return False


def build_event(levers: frozenset[Lever], denied: bool) -> dict[str, object]:
    """The ``tool_use`` event the configuration turn prints."""
    state: dict[str, object] = {
        "status": "error" if denied else "completed",
        "input": {"command": "printf config-isolation-probe"},
    }
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
