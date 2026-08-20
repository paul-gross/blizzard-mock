"""The concept-to-store routing table (``canon:one-owner``).

Declares, per seedable concept, which store(s) serve it — the single place a
verb's ``--store`` is checked against, and the single home for the refusal
wording. Store-neutral: no click, no SQLAlchemy (``bzh:domain-core``).
"""

from __future__ import annotations

HUB = "hub"
RUNNER = "runner"

#: Concept name -> the store(s) that serve it. Naming both dispatches on ``--store``;
#: naming one refuses the other via :func:`store_mismatch_message`.
STORE_TARGETS: dict[str, frozenset[str]] = {
    "runner": frozenset({HUB}),
    "graph": frozenset({HUB}),
    "chunk": frozenset({HUB}),
    "artifact": frozenset({HUB}),
    "usage": frozenset({HUB, RUNNER}),
    "lease": frozenset({HUB, RUNNER}),
    "escalation": frozenset({HUB}),
    "question": frozenset({HUB}),
    "event": frozenset({HUB}),
    "runner-pause": frozenset({HUB}),
    "transcript-segment": frozenset({RUNNER}),
}


def store_mismatch_message(concept: str, allowed: frozenset[str]) -> str:
    """The refusal wording for ``concept`` invoked against a store it doesn't serve.

    Only single-store concepts reach here — a two-store concept dispatches instead —
    so ``allowed`` always holds exactly one store to name.
    """
    (only,) = allowed
    return f"'{concept}' lives in the {only} store (--store {only})"
