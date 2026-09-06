"""Mirror-vs-real wire parity, checked mechanically against the sibling ``blizzard``.

The mock hub's response models and its runner-fact vocabulary mirror a wire surface this
repo cannot import. Both are compared here against the committed hub OpenAPI and the
committed fact-kind constants, so a real-side wire change that outruns the mirror fails a
mock-side gate (issue #277).

The sibling worktree is a hard requirement for the two tests that read it, not a skip: an
unresolvable ``blizzard`` refuses a green rather than reporting parity it never checked.
``$BLIZZARD_SOURCE`` overrides the default sibling path, as ``--context-root`` and
``$BLIZZARD_MOCK_WINTER_SOURCE`` do for the neighbouring cross-repo tools.
"""

from __future__ import annotations

import inspect
import json
import os
import re
from collections.abc import Callable
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from pydantic import BaseModel

import blizzard_mock.mock_hub.domain.wire as mirror
from blizzard_mock.mock_hub.api import deps
from blizzard_mock.mock_hub.domain import service as hub_service
from blizzard_mock.mock_hub.domain import state as hub_state
from blizzard_mock.mock_runner.domain import gateway as runner_gateway
from blizzard_mock.mock_runner.domain import service as runner_service

_BLIZZARD = Path(os.environ.get("BLIZZARD_SOURCE") or Path(__file__).resolve().parents[2] / "blizzard")
_HUB_SPEC = _BLIZZARD / "openapi" / "hub.openapi.json"
_FACT_KINDS_SOURCE = _BLIZZARD / "src" / "blizzard" / "wire" / "facts.py"


def _sibling(path: Path) -> str:
    """Read a file from the sibling ``blizzard`` worktree, or fail naming why — the one
    place either sibling-reading test resolves it, so neither can raise a bare traceback."""
    assert path.is_file(), (
        f"no sibling blizzard worktree at {_BLIZZARD} (expected {path}) — parity is unverifiable, "
        f"not green; set $BLIZZARD_SOURCE if it lives elsewhere"
    )
    return path.read_text()


#: Mirror model -> the hub schema it mirrors, plus the real fields it deliberately omits.
#: A mirror model missing from this map fails, so a new one is mapped on purpose.
_MIRRORED: dict[str, tuple[str, frozenset[str]]] = {
    "ApplyResponse": ("ApplyResponse", frozenset()),
    "BlockedView": ("BlockedView", frozenset()),
    "ChunkDetail": (
        "ChunkDetail",
        # The runner reads identity, fence, route, escalation, and questions; the rest of
        # the operator aggregate is optional on the real schema and never mirrored.
        frozenset(
            {
                "artifacts",
                "awaiting_external_merge",
                "blocked",
                "bounces",
                "cost",
                "current_node_name",
                "decision",
                "graph_created_at",
                "graph_name",
                "history",
                "intended_migration",
                "landed",
                "migrations",
                "open_prs",
                "pause",
                "pending",
                # Fence-relevant on the real side — `Fenced.out` reads it — but the mock
                # models the fence through `latest_epoch`, with no restart concept to fill it.
                "restarts",
                "usage",
            }
        ),
    ),
    "EnvelopeChoice": ("EnvelopeChoice", frozenset()),
    "ChunkEscalationView": ("ChunkEscalationView", frozenset()),
    "ExternalSubscriptionUsageView": ("ExternalSubscriptionUsageView", frozenset()),
    "ExternalSubscriptionUsageWindowView": ("ExternalSubscriptionUsageWindowView", frozenset()),
    "FindingView": ("FindingView", frozenset()),
    # Every proposal this mock serves is open by construction — no closure lever exists
    # to represent one, so the field is never mirrored.
    "GardenProposalView": ("GardenProposalView", frozenset({"closure"})),
    "GraphArtifact": ("GraphArtifact", frozenset()),
    "HubAdvanceResponse": ("HubAdvanceResponse", frozenset()),
    "LeaseTranscriptView": ("LeaseTranscriptView", frozenset()),
    # `proposes_work_items` authorizes a completion's `proposals` hub-side; no runner path
    # reads it, so a mirror serving a real runner carries nothing by carrying it.
    "NodeConfig": ("NodeConfig", frozenset({"proposes_work_items"})),
    "NodeEnvelope": ("NodeEnvelope", frozenset()),
    "QuestionView": ("QuestionView", frozenset()),
    "QueuePeekEntry": ("QueuePeekEntry", frozenset()),
    "QueuePeekResponse": ("QueuePeekResponse", frozenset()),
    "RotatePolicyView": ("blizzard__wire__envelope__RotatePolicyView", frozenset()),
    "RouteClaimResponse": ("RouteClaimResponse", frozenset()),
    "RouteTokenRekeyResponse": ("RouteTokenRekeyResponse", frozenset()),
    "RouteView": ("RouteView", frozenset()),
    "RunnerFactAck": ("RunnerFactAck", frozenset()),
    "RunnerView": ("RunnerView", frozenset()),
    "SubscriptionUsageView": ("SubscriptionUsageView", frozenset()),
    "SystemArtifactView": ("SystemArtifactView", frozenset()),
    "TranscriptSegmentAck": ("TranscriptSegmentAck", frozenset()),
    "WorkItemAuthorView": ("WorkItemAuthorView", frozenset()),
    "WorkItemEntry": ("WorkItemEntry", frozenset()),
    "WorkItemsView": ("WorkItemsView", frozenset()),
}

#: Mirrors with no schema to compare against, and why. ``RouteClaimConflict`` is the real
#: claim route's 409 body, which the real hub declares no response model for — so it is
#: absent from the spec entirely and this guard cannot reach it.
_UNSCHEMAED = {"RouteClaimConflict"}

#: Request-BODY mirrors, which live in ``mock_hub.api.deps`` rather than the mirror module
#: above and so are invisible to ``_mirror_models``. Only the transcript lane's five are
#: mapped here (blizzard#246): their own docstrings rest the rename defense on being typed
#: and required, and `ToolCallSegmentBody.input_truncated` is now defaulted, which alone
#: would let a rename of that field pass validation silently.
#: The two recursive views carry FastAPI's ``-Input``/``-Output`` split; a request body
#: mirrors the ``-Input`` half by construction.
_MIRRORED_BODIES: dict[str, tuple[str, frozenset[str]]] = {
    "SidechainSegmentBody": ("SidechainSegmentView-Input", frozenset()),
    "ToolCallSegmentBody": ("ToolCallSegmentView", frozenset()),
    "TranscriptSegmentBatchBody": ("TranscriptSegmentBatch", frozenset()),
    "TranscriptSegmentRecordBody": ("TranscriptSegmentRecord", frozenset()),
    "TurnSegmentBody": ("TurnSegmentView-Input", frozenset()),
}


def _mirror_models() -> dict[str, type[BaseModel]]:
    return {
        name: obj
        for name, obj in vars(mirror).items()
        if inspect.isclass(obj) and issubclass(obj, BaseModel) and obj.__module__ == mirror.__name__
    }


def _deps_mirror_bodies() -> dict[str, type[BaseModel]]:
    """Every ``deps`` request body marked ``MirroredWireBody`` (F10) — the request-body
    counterpart to ``_mirror_models``'s module-membership scan, since ``deps`` also holds
    bodies that are deliberately NOT field-for-field mirrors."""
    return {
        name: obj
        for name, obj in vars(deps).items()
        if inspect.isclass(obj) and issubclass(obj, deps.MirroredWireBody) and obj is not deps.MirroredWireBody
    }


def _hub_schemas() -> dict[str, Any]:
    return json.loads(_sibling(_HUB_SPEC))["components"]["schemas"]


def _wire_field_names(model: type[BaseModel]) -> set[str]:
    """A model's own field set, on the wire — a field's alias when it carries one (a
    Python keyword like `class` mirrored as `class_`), its Python name otherwise. The
    real schema's `properties` are alias-shaped throughout, so comparing against raw
    `model_fields` keys would flag every aliased field as both missing and extra."""
    return {f.alias or name for name, f in model.model_fields.items()}


def test_every_mirror_model_is_mapped_to_a_real_schema() -> None:
    assert set(_mirror_models()) == set(_MIRRORED) | _UNSCHEMAED


def test_every_mirrored_wire_body_is_mapped_to_a_real_schema() -> None:
    """F10 (review round 8): ``_MIRRORED_BODIES`` used to be checked only from below —
    every KEY in it was field-diffed, but nothing asserted the map was complete, so a new
    ``MirroredWireBody`` added to ``deps`` without a matching entry shipped silently
    unchecked. Mirrors ``test_every_mirror_model_is_mapped_to_a_real_schema`` above."""
    assert set(_deps_mirror_bodies()) == set(_MIRRORED_BODIES)


@pytest.mark.parametrize("name", sorted(_MIRRORED_BODIES))
def test_transcript_body_field_set_agrees_with_the_real_schema(name: str) -> None:
    """The same field-set diff for the transcript lane's request bodies, which
    ``_mirror_models`` cannot see (`bzh:wire-change-extends-mock`)."""
    schema_name, omitted = _MIRRORED_BODIES[name]
    schemas = _hub_schemas()
    assert schema_name in schemas, f"{schema_name} is gone from the hub spec — the mirror names a schema that left"
    real = set(schemas[schema_name].get("properties", {}))
    mirrored = _wire_field_names(getattr(deps, name))
    assert mirrored - real == set(), f"{name} carries fields the real schema has not: {sorted(mirrored - real)}"
    assert real - mirrored == omitted, f"{name} omits {sorted(real - mirrored)}, declared {sorted(omitted)}"


@pytest.mark.parametrize("name", sorted(_MIRRORED))
def test_mirror_field_set_agrees_with_the_real_schema(name: str) -> None:
    schema_name, omitted = _MIRRORED[name]
    schemas = _hub_schemas()
    assert schema_name in schemas, f"{schema_name} is gone from the hub spec — the mirror names a schema that left"
    real = set(schemas[schema_name].get("properties", {}))
    mirrored = _wire_field_names(_mirror_models()[name])
    assert mirrored - real == set(), f"{name} carries fields the real schema has not: {sorted(mirrored - real)}"
    assert real - mirrored == omitted, f"{name} omits {sorted(real - mirrored)}, declared {sorted(omitted)}"


def test_accepted_fact_kinds_match_the_real_vocabulary() -> None:
    """The batched ``/events`` dispatch and the real ``wire/facts`` constants name the same
    kinds — a real-side kind the mock never learned would be rejected, silently."""
    real = set(re.findall(r'^[A-Z_]+ = "([a-z_]+\.[a-z_]+)"$', _sibling(_FACT_KINDS_SOURCE), re.MULTILINE))
    mirrored = {
        value for name, value in vars(hub_service).items() if name.isupper() and isinstance(value, str) and "." in value
    }
    assert mirrored == real


def _transposable(entry_point: Callable[..., Any]) -> list[str]:
    """Positional parameter names whose annotation is shared with another positional one."""
    try:
        parameters = inspect.signature(entry_point).parameters.values()
    except (TypeError, ValueError):
        return []
    positional = [p for p in parameters if p.name != "self" and p.kind in (p.POSITIONAL_OR_KEYWORD, p.POSITIONAL_ONLY)]
    annotations = [str(p.annotation) for p in positional]
    return [p.name for p in positional if annotations.count(str(p.annotation)) > 1]


@pytest.mark.parametrize(
    "module", [hub_service, hub_state, runner_service, runner_gateway], ids=lambda m: m.__name__.split(".")[-2]
)
def test_no_mirror_entry_point_takes_transposable_positional_arguments(module: ModuleType) -> None:
    """Two adjacent same-typed positional parameters swap silently at a call site and the
    mock answers a plausible wrong thing, so every such parameter is keyword-only."""
    offenders = [
        f"{cls.__name__}.{fname}({', '.join(shared)})"
        for cls in vars(module).values()
        if inspect.isclass(cls) and cls.__module__ == module.__name__
        for fname, fun in vars(cls).items()
        if callable(fun) and (not fname.startswith("_") or fname == "__init__")
        for shared in [_transposable(fun)]
        if shared
    ]
    assert not offenders, f"transposable positional parameters: {offenders}"
