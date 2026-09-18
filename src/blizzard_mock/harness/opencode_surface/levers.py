"""The CLI-surface lever vocabulary a ``mock-opencode emit`` artifact bakes in.

A sibling of ``mock_hub``'s/the IdP's lever modules, not a reuse: those are
armed over HTTP against a long-lived service, while this one is stdlib-only
and baked into ``_baked.py`` as an immutable constant at ``emit`` time."""

from __future__ import annotations

from collections.abc import Iterable
from enum import StrEnum


class Lever(StrEnum):
    """One member per fake-OpenCode misbehaviour — see :data:`CATALOG` for prose.

    Values are uppercase, unlike the sibling ``HubLever`` (lowercase): they are the
    ``--lever NAME`` strings ``blizzard``'s own tests hardcode as a public CLI contract."""

    #: Tool call errors unrelated, never denied.
    PERMISSION_REQUEST_ONLY = "PERMISSION_REQUEST_ONLY"
    #: Prose-only denial, no ``tool_use`` error part.
    PERMISSION_PROSE_ONLY = "PERMISSION_PROSE_ONLY"
    #: The denial event is doubled.
    PERMISSION_DUPLICATE = "PERMISSION_DUPLICATE"
    #: The denial is a bare OS ``permission denied`` string, not the usual prose.
    PERMISSION_OS_ERROR = "PERMISSION_OS_ERROR"
    #: The permission turn exits 9 instead of 0.
    PERMISSION_NONZERO = "PERMISSION_NONZERO"
    #: ``debug config --pure`` reports a competing model; other turns skip the permission check.
    IGNORE_CONFIG = "IGNORE_CONFIG"
    #: ``debug config --pure``'s effective config drops the ``shell`` key.
    DROP_CONFIG_SHELL = "DROP_CONFIG_SHELL"
    #: ``debug config --pure``'s effective config drops the ``compaction`` key.
    DROP_CONFIG_COMPACTION = "DROP_CONFIG_COMPACTION"
    #: The default run turn answers a provider ``APIError`` (429 usage-limit), then hangs.
    PROVIDER_REFUSAL = "PROVIDER_REFUSAL"
    #: The configuration turn's denial is plain prose, no ``tool_use`` error part.
    CONFIGURATION_PROSE_ONLY = "CONFIGURATION_PROSE_ONLY"
    #: The configuration turn's denial is a bare OS ``permission denied`` string.
    CONFIGURATION_OS_ERROR = "CONFIGURATION_OS_ERROR"
    #: ``export`` always reports the tool call completed, even while it is still live.
    STATIC_REPLAY = "STATIC_REPLAY"
    #: A fresh (non-resumed) default run turn exits 7.
    FRESH_NONZERO = "FRESH_NONZERO"
    #: The process-control turn never writes live state.
    PROCESS_CONTROL_NO_LIVE_STATE = "PROCESS_CONTROL_NO_LIVE_STATE"
    #: ``session``/``export`` report the wrong directory.
    TAKEOVER_WRONG_DIRECTORY = "TAKEOVER_WRONG_DIRECTORY"
    #: The served/exported/default session id is ``ses_wrong``.
    TAKEOVER_WRONG_SESSION = "TAKEOVER_WRONG_SESSION"
    #: The event stream serves JSON instead of an SSE comment.
    TAKEOVER_NON_SSE = "TAKEOVER_NON_SSE"
    #: ``attach`` exits 0 before ever reading the takeover prompt from stdin.
    TAKEOVER_EXIT_EARLY = "TAKEOVER_EXIT_EARLY"
    #: The event stream (and ``attach``) go idle forever, with no body/Content-Length.
    TAKEOVER_IDLE_SSE = "TAKEOVER_IDLE_SSE"
    #: The event stream closes immediately with an empty body.
    TAKEOVER_IMMEDIATE_EOF = "TAKEOVER_IMMEDIATE_EOF"
    #: The event stream answers 503 with a plain-text failure body.
    TAKEOVER_STREAM_FAILURE = "TAKEOVER_STREAM_FAILURE"
    #: The event stream blocks until a ``POST /session`` is observed.
    TAKEOVER_EVENT_GATED = "TAKEOVER_EVENT_GATED"
    #: The security-proof turn actually runs the embedded shell command.
    SECURITY_COMMAND_EXECUTES = "SECURITY_COMMAND_EXECUTES"
    #: ``--version`` overwrites the on-disk ``auth.json``.
    MUTATE_AUTH = "MUTATE_AUTH"
    #: ``--version`` records ``auth.json``'s present/missing/unreadable status to a marker.
    READ_AUTH = "READ_AUTH"
    #: ``POST .../summarize`` leaves session state unchanged.
    COMPACTION_NO_CHANGE = "COMPACTION_NO_CHANGE"


CATALOG: dict[str, str] = {
    Lever.PERMISSION_REQUEST_ONLY.value: "permission turn: tool call errors unrelated, never denied",
    Lever.PERMISSION_PROSE_ONLY.value: "permission turn: prose-only denial, no tool_use error part",
    Lever.PERMISSION_DUPLICATE.value: "permission turn / debug agent --tool: denial doubled",
    Lever.PERMISSION_OS_ERROR.value: "permission turn: denial is a bare OS 'permission denied' string",
    Lever.PERMISSION_NONZERO.value: "permission turn: exits 9 instead of 0",
    Lever.IGNORE_CONFIG.value: (
        "debug config --pure reports a competing model; debug agent/configuration turns skip the permission check"
    ),
    Lever.DROP_CONFIG_SHELL.value: "debug config --pure: effective config drops 'shell'",
    Lever.DROP_CONFIG_COMPACTION.value: "debug config --pure: effective config drops 'compaction'",
    Lever.PROVIDER_REFUSAL.value: "default run turn: provider APIError (429 usage-limit), then hangs",
    Lever.CONFIGURATION_PROSE_ONLY.value: "configuration turn: plain denial prose, no tool_use error part",
    Lever.CONFIGURATION_OS_ERROR.value: "configuration turn: denial is a bare OS 'permission denied' string",
    Lever.STATIC_REPLAY.value: "export always reports the tool call completed, even while live",
    Lever.FRESH_NONZERO.value: "a fresh (non-resumed) default run turn exits 7",
    Lever.PROCESS_CONTROL_NO_LIVE_STATE.value: "process-control turn never writes live state",
    Lever.TAKEOVER_WRONG_DIRECTORY.value: "session/export report the wrong directory",
    Lever.TAKEOVER_WRONG_SESSION.value: "served/exported/default session id is 'ses_wrong'",
    Lever.TAKEOVER_NON_SSE.value: "event stream serves JSON instead of an SSE comment",
    Lever.TAKEOVER_EXIT_EARLY.value: "attach exits 0 before reading the takeover prompt from stdin",
    Lever.TAKEOVER_IDLE_SSE.value: "event stream (and attach) go idle forever, no body/Content-Length",
    Lever.TAKEOVER_IMMEDIATE_EOF.value: "event stream closes immediately with an empty body",
    Lever.TAKEOVER_STREAM_FAILURE.value: "event stream answers 503 with a plain-text failure body",
    Lever.TAKEOVER_EVENT_GATED.value: "event stream blocks until a POST /session is observed",
    Lever.SECURITY_COMMAND_EXECUTES.value: "security-proof turn actually runs the embedded shell command",
    Lever.MUTATE_AUTH.value: "--version overwrites the on-disk auth.json",
    Lever.READ_AUTH.value: "--version records auth.json's present/missing/unreadable status to a marker",
    Lever.COMPACTION_NO_CHANGE.value: "POST .../summarize leaves session state unchanged",
}


def parse(names: Iterable[str]) -> frozenset[Lever]:
    """Validate ``names`` against :class:`Lever`; raise naming every bad one.

    Plural-safe: every unrecognized token is reported in one error, not just the
    first one encountered.
    """
    given = list(names)
    valid = {member.value: member for member in Lever}
    bad = sorted({name for name in given if name not in valid})
    if bad:
        raise ValueError(f"unknown opencode lever name(s): {', '.join(bad)}")
    return frozenset(valid[name] for name in given)


__all__ = ["CATALOG", "Lever", "parse"]
