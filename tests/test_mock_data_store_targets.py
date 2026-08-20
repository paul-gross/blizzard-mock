"""Unit coverage for the concept-to-store routing table (``blizzard-mock:unit-test``).

Pure, no store, no click (``bzh:domain-core``) — ``cli.py``'s ``_require_store`` is
what turns a refusal into a ``click.UsageError``; this module only declares the table
and the wording.
"""

from __future__ import annotations

from blizzard_mock.mock_data.domain.store_targets import HUB, RUNNER, STORE_TARGETS, store_mismatch_message

#: Concepts store-polymorphic across hub and runner — everything else still lives in exactly one store.
_MULTI_STORE = {"lease", "usage"}


def test_every_hub_only_concept_is_declared_that_way() -> None:
    assert STORE_TARGETS
    for concept, allowed in STORE_TARGETS.items():
        if concept in _MULTI_STORE:
            continue
        assert allowed in (frozenset({HUB}), frozenset({RUNNER})), concept


def test_lease_and_usage_serve_both_stores() -> None:
    for concept in _MULTI_STORE:
        assert STORE_TARGETS[concept] == frozenset({HUB, RUNNER}), concept


def test_transcript_segment_is_runner_only() -> None:
    assert STORE_TARGETS["transcript-segment"] == frozenset({RUNNER})


def test_store_mismatch_message_names_the_one_allowed_store() -> None:
    assert store_mismatch_message("chunk", frozenset({HUB})) == "'chunk' lives in the hub store (--store hub)"
