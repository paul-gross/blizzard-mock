"""Pure transitions over the cross-invocation session-state document.

A small, loosely-shaped JSON dict (``phase``, ``compaction_generation``,
``takeover_prompt``, each key optional); ``internal.state_store`` reads/writes
it under ``$XDG_STATE_HOME``. A missing file reads as ``{}``."""

from __future__ import annotations


def process_control_state() -> dict:
    """The state a process-control turn writes: a full reset to ``phase=process_control``."""
    return {"phase": "process_control"}


def running_state() -> dict:
    """The state a fresh ``sleep 5`` turn writes before it sleeps."""
    return {"phase": "running"}


def done_state() -> dict:
    """The state a ``sleep 5`` turn writes once it wakes."""
    return {"phase": "done"}


def summarized_state(state: dict) -> dict:
    """The state ``POST .../summarize`` writes: phase flips, the generation counts up."""
    return {**state, "phase": "summarized", "compaction_generation": int(state.get("compaction_generation", 0)) + 1}


def takeover_continued_state(state: dict, prompt: str) -> dict:
    """The state an ``attach`` takeover records once the operator's line arrives."""
    return {**state, "phase": "takeover_continued", "takeover_prompt": prompt}


def is_running(state: dict, *, static_replay: bool) -> bool:
    """Whether ``export`` should render the long-running tool call as still live."""
    return state.get("phase") in {"running", "process_control"} and not static_replay
