"""Composes one ``escalations`` row (``blizzard/hub/store/schema.py``, ``bzh:facts-not-status``).

The real ``escalations.takeover_command`` is always the identical shape regardless of
*why* the chunk was parked — ``blizzard.runner.loop.steps._escalate`` composes it once,
from the harness adapter's ``resume_command`` (``cd <workdir> && <binary> --resume
<session_id>``), for both a retries-exhausted park and :func:`_park_on_cost_cap`'s
spend-cap park; the cap-specific wording (``f"spend cap ${cap:.2f} reached (spend
${cost:.2f})"``, ``_park_on_cost_cap``'s ``reason``) is **log-line prose only** — it is
never written to any column, so there is no live-schema text this module could read
back verbatim. ``--cause cap`` therefore folds that same reason phrasing (placeholder
amounts, no flags name real ones) onto the front of the generic resume-command
placeholder, so a chunk seeded this way is recognizable as a cap-park — a deliberate,
documented mock-only synthesis, not a literal reproduction of a real stored value.
``--cause retries`` (the default) carries no such prefix, matching the real schema's
own retries-exhausted shape exactly.

``wrapped_takeover_command`` defaults the same way regardless of ``cause``: a real
spend-cap park reuses the identical ``_escalate`` composition a retries-exhausted park
does (``runner/loop/steps.py``), so both ``--cause retries`` and ``--cause cap`` get a
synthesized placeholder ``blizzard runner takeover`` command alongside
``takeover_command`` unless ``--wrapped-takeover-command`` overrides it explicitly.
The wrapped default follows the raw value — an explicitly emptied ``takeover_command``
suppresses it, and an explicit wrapped command alongside an empty raw one is rejected —
because wrapped implies raw, never the reverse (``humans.md`` §Escalation).
"""

from __future__ import annotations

from datetime import datetime

from blizzard_mock.mock_data.domain.facts import FactRow

CAUSE_RETRIES = "retries"
CAUSE_CAP = "cap"

#: The two causes ``compose_escalation`` accepts.
CAUSES = (CAUSE_RETRIES, CAUSE_CAP)

#: The generic placeholder resume command every plain retries-exhausted escalation
#: carries — the same shape ``domain/chunk_seed.py``'s ``needs_human`` status composes.
_RETRIES_TAKEOVER_TEMPLATE = "cd <workdir> && <resume {chunk_id}>"

#: ``--cause cap``'s default — mirrors ``_park_on_cost_cap``'s log-only reason wording
#: (see module docstring) prefixed onto the generic resume-command placeholder.
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

    ``takeover_command`` explicitly supplied overrides either ``cause`` default
    verbatim. Otherwise ``cause`` selects the default: ``cap`` composes the
    recognizable spend-cap wording (see module docstring), ``retries`` (the default)
    the plain generic resume-command placeholder. ``wrapped_takeover_command``
    explicitly supplied overrides its own default the same way; left unset, its
    default is the synthesized placeholder ``blizzard runner takeover`` command
    regardless of ``cause`` (see module docstring) — but only when the resolved
    ``takeover_command`` is non-empty, because wrapped implies raw, never the
    reverse (``blizzard-context:/domain/humans.md`` §Escalation). Raises
    :class:`EscalationCompositionError` for an unknown ``cause``, or for an
    explicit wrapped command alongside an empty raw one — that row shape is
    impossible in the real store, so it fails loud rather than seeding it.
    """
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
