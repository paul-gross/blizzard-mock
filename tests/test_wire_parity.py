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
    "ChunkDetail": (
        "ChunkDetail",
        # The runner reads identity, fence, route, escalation, and questions; the rest of
        # the operator aggregate is optional on the real schema and never mirrored.
        frozenset(
            {
                "artifacts",
                "awaiting_external_merge",
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
                "usage",
            }
        ),
    ),
    "EnvelopeChoice": ("EnvelopeChoice", frozenset()),
    "EscalationView": ("EscalationView", frozenset()),
    "ExternalSubscriptionUsageView": ("ExternalSubscriptionUsageView", frozenset()),
    "ExternalSubscriptionUsageWindowView": ("ExternalSubscriptionUsageWindowView", frozenset()),
    "HubAdvanceResponse": ("HubAdvanceResponse", frozenset()),
    "LeaseTranscriptView": ("LeaseTranscriptView", frozenset()),
    "NodeConfig": ("NodeConfig", frozenset()),
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
    "TranscriptSegmentAck": ("TranscriptSegmentAck", frozenset()),
    "WorkItemEntry": ("WorkItemEntry", frozenset()),
    "WorkItemsView": ("WorkItemsView", frozenset()),
}

#: Mirrors with no schema to compare against, and why. ``RouteClaimConflict`` is the real
#: claim route's 409 body, which the real hub declares no response model for — so it is
#: absent from the spec entirely and this guard cannot reach it.
_UNSCHEMAED = {"RouteClaimConflict"}


def _mirror_models() -> dict[str, type[BaseModel]]:
    return {
        name: obj
        for name, obj in vars(mirror).items()
        if inspect.isclass(obj) and issubclass(obj, BaseModel) and obj.__module__ == mirror.__name__
    }


def _hub_schemas() -> dict[str, Any]:
    return json.loads(_sibling(_HUB_SPEC))["components"]["schemas"]


def test_every_mirror_model_is_mapped_to_a_real_schema() -> None:
    assert set(_mirror_models()) == set(_MIRRORED) | _UNSCHEMAED


@pytest.mark.parametrize("name", sorted(_MIRRORED))
def test_mirror_field_set_agrees_with_the_real_schema(name: str) -> None:
    schema_name, omitted = _MIRRORED[name]
    schemas = _hub_schemas()
    assert schema_name in schemas, f"{schema_name} is gone from the hub spec — the mirror names a schema that left"
    real = set(schemas[schema_name].get("properties", {}))
    mirrored = set(_mirror_models()[name].model_fields)
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
