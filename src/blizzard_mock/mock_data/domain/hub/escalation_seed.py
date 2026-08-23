"""Composes one ``escalations`` row (``bzh:facts-not-status``).

``--cause cap`` folds a spend-cap reason phrasing onto the generic
resume-command placeholder; ``--cause retries`` (default) carries no prefix.
Wrapped-vs-raw rules: `blizzard-context:/domain/humans/escalation.md` §Escalation.
"""

from __future__ import annotations

from datetime import datetime

from blizzard_mock.mock_data.domain.facts import FactRow

CAUSE_RETRIES = "retries"
CAUSE_CAP = "cap"

#: The two causes ``compose_escalation`` accepts.
CAUSES = (CAUSE_RETRIES, CAUSE_CAP)

#: The generic placeholder resume command every plain retries-exhausted
#: escalation carries.
_RETRIES_TAKEOVER_TEMPLATE = "cd <workdir> && <resume {chunk_id}>"

#: ``--cause cap``'s default: the recognizable spend-cap wording prefixed onto
#: the generic resume-command placeholder.
_CAP_TAKEOVER_TEMPLATE = "spend cap <cap> reached (spend <spend>) — cd <workdir> && <resume {chunk_id}>"

#: The wrapped entry point's placeholder (issue #251), mirroring the real runner's own
#: composition. Both causes' defaults reach this (see module docstring).
_WRAPPED_TAKEOVER_TEMPLATE = "blizzard runner takeover {chunk_id} --dir <runner-dir>"


class EscalationCompositionError(Exception):
    """A ``--cause`` :func:`compose_escalation` cannot honor."""


def compose_escalation(
    *,
    chunk_id: str,
    epoch: int,
    recorded_at: datetime,
    cause: str = CAUSE_RETRIES,
    takeover_command: str | None = None,
    wrapped_takeover_command: str | None = None,
    decision_id: str | None = None,
) -> FactRow:
    """One ``escalations`` row.

    ``takeover_command``/``wrapped_takeover_command`` explicit override the
    ``cause`` defaults; raises :class:`EscalationCompositionError` for an
    unknown ``cause`` or a bad wrapped/raw pairing."""
    if cause not in CAUSES:
        raise EscalationCompositionError(f"unknown cause {cause!r} — one of {CAUSES}")
    if takeover_command is None:
        template = _CAP_TAKEOVER_TEMPLATE if cause == CAUSE_CAP else _RETRIES_TAKEOVER_TEMPLATE
        takeover_command = template.format(chunk_id=chunk_id)
    if wrapped_takeover_command is None:
        wrapped_takeover_command = _WRAPPED_TAKEOVER_TEMPLATE.format(chunk_id=chunk_id) if takeover_command else ""
    elif wrapped_takeover_command and not takeover_command:
        raise EscalationCompositionError(
            "wrapped_takeover_command without takeover_command — wrapped implies raw, never the reverse"
        )
    return FactRow(
        table="escalations",
        values={
            "chunk_id": chunk_id,
            "epoch": epoch,
            "takeover_command": takeover_command,
            "wrapped_takeover_command": wrapped_takeover_command,
            "decision_id": decision_id,
            "recorded_at": recorded_at,
        },
    )
