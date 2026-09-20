"""Mirror-vs-real wire parity, checked mechanically against the sibling ``blizzard``.

The mock hub's response models and its runner-fact vocabulary mirror a wire surface this
repo cannot import. Both are compared here against the committed hub OpenAPI and the
committed fact-kind constants, so a real-side wire change that outruns the mirror fails a
mock-side gate (issue #277). ``mock-opencode``'s facade wire is checked the same way, but
against code rather than a schema document: its actual stdout is fed through blizzard's own
production ``opencode_shapes.parse_run_jsonl`` (D10), since a facade whose event stream the
real parser rejects is a mock nothing downstream can trust.

The sibling worktree is a hard requirement for every test here that reads it, not a skip: an
unresolvable ``blizzard`` refuses a green rather than reporting parity it never checked.
``$BLIZZARD_SOURCE`` overrides the default sibling path, as ``--context-root`` and
``$BLIZZARD_MOCK_WINTER_SOURCE`` do for the neighbouring cross-repo tools.
"""

from __future__ import annotations

import importlib.util
import inspect
import json
import os
import re
import subprocess
import sys
from collections.abc import Callable, Mapping
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
_OPENCODE_SHAPES_SOURCE = _BLIZZARD / "src" / "blizzard" / "runner" / "harness" / "internal" / "opencode_shapes.py"


def _sibling(path: Path) -> str:
    """Read a file from the sibling ``blizzard`` worktree, or fail naming why — the one
    place either sibling-reading test resolves it, so neither can raise a bare traceback."""
    assert path.is_file(), (
        f"no sibling blizzard worktree at {_BLIZZARD} (expected {path}) — parity is unverifiable, "
        f"not green; set $BLIZZARD_SOURCE if it lives elsewhere"
    )
    return path.read_text()


def _load_opencode_shapes() -> ModuleType:
    """Import blizzard's real ``opencode_shapes`` parser straight off the sibling worktree.

    This repo cannot depend on ``blizzard`` as a package, so the module is loaded from its
    source file directly — it is dependency-free (stdlib only), so this is not a partial or
    stubbed import, it is the exact parser production code runs."""
    path = _OPENCODE_SHAPES_SOURCE
    assert path.is_file(), (
        f"no sibling blizzard worktree at {_BLIZZARD} (expected {path}) — parity is unverifiable, "
        f"not green; set $BLIZZARD_SOURCE if it lives elsewhere"
    )
    spec = importlib.util.spec_from_file_location("_opencode_shapes_wire_parity", path)
    assert spec is not None and spec.loader is not None, f"could not load a module spec from {path}"
    module = importlib.util.module_from_spec(spec)
    # Registered before exec: the module's own `@dataclass`-decorated classes resolve their
    # (all-string, `from __future__ import annotations`) field annotations by looking their
    # module back up in `sys.modules` — unregistered, that lookup finds nothing and every
    # dataclass in the file fails to define at class-creation time.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _run_mock_opencode(cwd: Path, env: Mapping[str, str], *args: str) -> subprocess.CompletedProcess[str]:
    """One real ``mock-opencode`` invocation, run exactly as the runner adapter would spawn
    it — the module entry point, not a hand-rendered stand-in for its output."""
    return subprocess.run(
        [sys.executable, "-m", "blizzard_mock.harness.facades.opencode", *args],
        cwd=cwd,
        env=dict(env),
        capture_output=True,
        text=True,
    )


def test_mock_opencode_fresh_turn_parses_through_the_real_run_jsonl_parser(fenced_repo) -> None:
    """D10: a fresh mint's actual stdout must parse cleanly through blizzard's own
    production parser, and its root session id must be server-assigned — the caller
    supplied no ``--session`` at all, exactly as the real fresh-session handshake
    requires (execution spec, "Fresh-session handshake")."""
    shapes = _load_opencode_shapes()
    cwd, env = fenced_repo
    proc = _run_mock_opencode(cwd, env, "run", "verdict('approve', 'looks good')")
    assert proc.returncode == 0, proc.stderr

    events = shapes.parse_run_jsonl(proc.stdout)

    assert events
    assert events[0].session_id  # server-assigned; never a caller-supplied hint


def test_mock_opencode_resume_parses_through_the_real_run_jsonl_parser(fenced_repo) -> None:
    """A resume never re-mints: it keeps the session id the caller learned off the fresh
    mint's own first record, and its stdout still parses cleanly through the real parser."""
    shapes = _load_opencode_shapes()
    cwd, env = fenced_repo
    first = _run_mock_opencode(cwd, env, "run", "ask('proceed?', ['yes', 'no'])")
    assert first.returncode == 0, first.stderr
    session_id = shapes.parse_run_jsonl(first.stdout)[0].session_id

    second = _run_mock_opencode(
        cwd, env, "run", "--session", session_id, "--variant", "high", "--auto", "verdict('pass', 'resumed')"
    )
    assert second.returncode == 0, second.stderr

    events = shapes.parse_run_jsonl(second.stdout)

    assert events
    assert events[0].session_id == session_id


def test_mock_opencode_error_turn_parses_through_the_real_run_jsonl_parser(fenced_repo) -> None:
    """A crashed turn's stdout still parses cleanly, carrying an explicit session error the
    real adapter's ``_session_error`` reads back — never a step-finish, since the turn never
    produced a usable completed step."""
    shapes = _load_opencode_shapes()
    cwd, env = fenced_repo
    proc = _run_mock_opencode(cwd, env, "run", "crash()")
    assert proc.returncode == 1

    events = shapes.parse_run_jsonl(proc.stdout)

    assert any(event.type == "error" for event in events)
    assert not any(event.type == "step_finish" for event in events)


def test_mock_opencode_permission_denial_parses_through_the_real_run_jsonl_parser(fenced_repo) -> None:
    """D7: ``permission_denial()`` stages a ``permission`` event whose stdout still parses
    cleanly through the real parser, carrying the denied permission's name and patterns."""
    shapes = _load_opencode_shapes()
    cwd, env = fenced_repo
    proc = _run_mock_opencode(
        cwd, env, "run", "permission_denial('bash', ['git push *']); verdict('deny', 'not allowed')"
    )
    assert proc.returncode == 0, proc.stderr

    events = shapes.parse_run_jsonl(proc.stdout)

    permission_events = [event for event in events if event.type == "permission"]
    assert len(permission_events) == 1
    permission = permission_events[0].permission
    assert permission is not None
    assert permission.id
    assert permission.permission == "bash"
    assert permission.patterns == ("git push *",)


def test_mock_opencode_interrupted_tool_parses_through_the_real_run_jsonl_parser(fenced_repo) -> None:
    """D7: ``interrupt_tool()`` stages a ``tool_use`` event whose part's ``state.status`` is
    ``"error"`` — an interruption reads on OpenCode's wire exactly as a tool failure does,
    since there is no separate "interrupted" discriminator — and it still parses cleanly."""
    shapes = _load_opencode_shapes()
    cwd, env = fenced_repo
    proc = _run_mock_opencode(
        cwd,
        env,
        "run",
        "interrupt_tool('bash', {'command': 'rm -rf /'}, error='interrupted by the user'); verdict('deny')",
    )
    assert proc.returncode == 0, proc.stderr

    events = shapes.parse_run_jsonl(proc.stdout)

    tool_events = [event for event in events if event.type == "tool_use"]
    assert len(tool_events) == 1
    part = tool_events[0].part
    assert part is not None
    assert part.tool == "bash"
    assert part.state is not None
    assert part.state.status == "error"
    assert part.state.error == "interrupted by the user"


def test_mock_opencode_malformed_record_fails_the_real_run_jsonl_parser(fenced_repo) -> None:
    """D7: ``malformed_record()`` stages a line the real parser genuinely rejects — proving
    the production rejection path is exercised against this mock, not only against
    blizzard's own hand-built fixtures."""
    shapes = _load_opencode_shapes()
    cwd, env = fenced_repo
    proc = _run_mock_opencode(cwd, env, "run", "malformed_record(); verdict('approve')")
    assert proc.returncode == 0, proc.stderr

    with pytest.raises(shapes.OpenCodeShapeError):
        shapes.parse_run_jsonl(proc.stdout)


def test_mock_opencode_permission_denial_honors_an_explicit_permission_id(fenced_repo) -> None:
    """F25: ``permission_id`` is a non-default kwarg no prior test exercised — the staged
    event must carry the caller's id rather than always minting a fresh one."""
    shapes = _load_opencode_shapes()
    cwd, env = fenced_repo
    proc = _run_mock_opencode(
        cwd, env, "run", "permission_denial('bash', permission_id='perm_fixed_1'); verdict('deny')"
    )
    assert proc.returncode == 0, proc.stderr

    events = shapes.parse_run_jsonl(proc.stdout)

    permission_events = [event for event in events if event.type == "permission"]
    assert len(permission_events) == 1
    permission = permission_events[0].permission
    assert permission is not None
    assert permission.id == "perm_fixed_1"


def test_mock_opencode_interrupted_tool_honors_an_explicit_call_id(fenced_repo) -> None:
    """F25: ``call_id`` is a non-default kwarg no prior test exercised — the staged tool
    part must carry the caller's call id rather than always minting a fresh one."""
    shapes = _load_opencode_shapes()
    cwd, env = fenced_repo
    proc = _run_mock_opencode(cwd, env, "run", "interrupt_tool('bash', call_id='call_fixed_1'); verdict('deny')")
    assert proc.returncode == 0, proc.stderr

    events = shapes.parse_run_jsonl(proc.stdout)

    tool_events = [event for event in events if event.type == "tool_use"]
    assert len(tool_events) == 1
    part = tool_events[0].part
    assert part is not None
    assert part.call_id == "call_fixed_1"


def test_mock_opencode_malformed_record_honors_an_explicit_line(fenced_repo) -> None:
    """F25: an explicit ``line`` is a non-default kwarg no prior test exercised — a
    caller-chosen rejection shape (here, valid JSON missing a required field) must reach
    the wire verbatim, not just the default invalid-JSON line."""
    shapes = _load_opencode_shapes()
    cwd, env = fenced_repo
    custom_line = '{"type": "tool_use", "sessionID": "s1"}'
    proc = _run_mock_opencode(cwd, env, "run", f"malformed_record(line={custom_line!r}); verdict('approve')")
    assert proc.returncode == 0, proc.stderr
    assert custom_line in proc.stdout

    with pytest.raises(shapes.OpenCodeShapeError):
        shapes.parse_run_jsonl(proc.stdout)


def test_permission_denial_refuses_on_a_non_opencode_wire(fenced_repo) -> None:
    """D7's misbehaviour plane is OpenCode-only — calling it from a facade whose wire has
    no JSONL stream to carry it fails the turn clearly rather than silently no-op'ing."""
    cwd, env = fenced_repo
    proc = subprocess.run(
        [sys.executable, "-m", "blizzard_mock.harness.facades.claude_code", "-p", "permission_denial('bash')"],
        cwd=cwd,
        env=dict(env),
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 1
    assert "OpenCode-only" in proc.stdout


#: Mirror model -> the hub schema it mirrors, plus the real fields it deliberately omits.
#: A mirror model missing from this map fails, so a new one is mapped on purpose.
_MIRRORED: dict[str, tuple[str, frozenset[str]]] = {
    "ApplyResponse": ("ApplyResponse", frozenset()),
    # The mock models one prerequisite at a time; the real default preserves that
    # compatible single-prerequisite view.
    "BlockedView": ("BlockedView", frozenset({"unmet_count"})),
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
                "neighborhood",
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
    "ChunkStatusView": ("ChunkStatusView", frozenset({"pause", "restart_epochs", "cost", "decision"})),
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
    "RotatePolicyView": ("RotatePolicyView", frozenset()),
    "RouteClaimResponse": ("RouteClaimResponse", frozenset()),
    "RouteTokenRekeyResponse": ("RouteTokenRekeyResponse", frozenset()),
    "RouteView": ("RouteView", frozenset()),
    "RunnerCapabilityView": ("RunnerCapability", frozenset()),
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

#: Request-body mirrors, invisible to ``_mirror_models`` since they live in ``mock_hub.api.deps``.
_MIRRORED_BODIES: dict[str, tuple[str, frozenset[str]]] = {
    "QueuePeekBody": ("QueuePeekRequest", frozenset()),
    "RunnerCapabilityBody": ("RunnerCapability", frozenset()),
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
