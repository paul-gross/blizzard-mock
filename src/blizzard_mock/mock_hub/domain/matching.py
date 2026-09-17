"""Capability-matched queue selection (blizzard#433 Phase 3) — the mock's own
reimplementation of ``blizzard.hub.domain.eligibility.EligibilityCheck`` and
``blizzard.hub.domain.queue.select_matched_entry``, over the mock's own flat
``ChunkState``/``NodeSpec`` graph shape rather than the real hub's ``Graph``/``Node``.

The mock's seed vocabulary already IS a graph: a :class:`~blizzard_mock.mock_hub.domain.models.NodeSpec`'s
``choices[].to`` are its outbound edges, so the DFS below walks them directly rather than
through a separate ``Graph.edges_from`` seam.
"""

from __future__ import annotations

from collections.abc import Sequence
from enum import Enum

from blizzard_mock.mock_hub.domain.models import TERMINAL, ChunkState, Executor, NodeSpec
from blizzard_mock.mock_hub.domain.state import RunnerCapability


class QueueMatchPolicy(Enum):
    """The matched fleet peek's hold-or-pass-over policy (D8) — applied to the
    capability-eligibility and blocked-dependency dimensions together, never one alone.
    :meth:`of` never raises: an unrecognized wire value reads as :attr:`PASS_OVER`,
    mirroring ``blizzard.hub.domain.queue.QueueMatchPolicy``."""

    HOLD = "hold"
    PASS_OVER = "pass-over"

    @classmethod
    def of(cls, value: str) -> QueueMatchPolicy:
        return cls.HOLD if value == cls.HOLD.value else cls.PASS_OVER


def _reachable_runner_node_ids(chunk: ChunkState, start: str) -> list[str]:
    """Every runner-owned node reached from ``start``, DFS over each node's own
    ``choices[].to`` edges, visiting each node id at most once (cycle-safe) — the mock's
    own mirror of ``EligibilityCheck._reachable_runner_nodes``. A hub node is traversed
    through but contributes nothing itself; a choice pointing at the reserved terminal
    ends its path there, evaluating nothing past it. A choice naming an unseeded node id
    is skipped defensively — the mock's seed vocabulary carries no referential-integrity
    check of its own at seed time."""
    seen: set[str] = set()
    runner_ids: list[str] = []
    stack = [start]
    while stack:
        node_id = stack.pop()
        if node_id in seen:
            continue
        seen.add(node_id)
        node = chunk.node(node_id)
        if node is None:
            continue
        if node.executor is Executor.RUNNER:
            runner_ids.append(node_id)
        for choice in node.choices:
            if choice.to != TERMINAL:
                stack.append(choice.to)
    return runner_ids


def _effective_harnesses(node: NodeSpec, chunk: ChunkState) -> list[str]:
    """A node's own declared harnesses, else the chunk's default — the mock's own stand-in
    for ``EffectiveSession.of``'s declaration-over-chunk-default merge; the mock's flat
    ``NodeSpec`` carries no separate named-session-declaration registry to resolve
    through, so the node's own ``session_harnesses`` stands in for a declaration."""
    return list(node.session_harnesses) if node.session_harnesses else list(chunk.default_harnesses)


def _effective_model(node: NodeSpec, chunk: ChunkState) -> list[str]:
    """The ``_effective_harnesses`` counterpart for the strict tier check's model
    preference."""
    return list(node.session_model) if node.session_model else list(chunk.default_model)


def _lineage_satisfied(chunk: ChunkState, node_id: str, capabilities: Sequence[RunnerCapability]) -> bool:
    """Whether some reported capability could serve ``node_id``'s effective session —
    the mock's own mirror of ``EligibilityCheck._lineage_satisfied``."""
    node = chunk.node(node_id)
    if node is None:
        return True  # unreachable defensively — an unseeded node id was never walked to
    harnesses = _effective_harnesses(node, chunk)
    if not harnesses:
        return any(capability.default for capability in capabilities)
    model = _effective_model(node, chunk)
    strict = len(harnesses) > 1 and bool(model)
    for harness_id in harnesses:
        capability = next((c for c in capabilities if c.harness_id == harness_id), None)
        if capability is None:
            continue
        if strict and not any(tier in capability.tiers for tier in model):
            continue
        return True
    return False


def capability_ineligible(chunk: ChunkState, node_id: str, capabilities: Sequence[RunnerCapability]) -> bool:
    """Whether ``capabilities`` cannot work ``chunk`` from ``node_id`` — the mock's own
    mirror of ``blizzard.hub.domain.queue._capability_ineligible``. Asserting no
    capabilities at all applies no filter, matching the legacy peek's unfiltered
    reach-ahead — a deliberate divergence from :func:`_lineage_satisfied`'s own empty-
    snapshot reading, exactly as the real hub's own divergence is documented."""
    if not capabilities:
        return False
    return not all(
        _lineage_satisfied(chunk, node_id, capabilities) for node_id in _reachable_runner_node_ids(chunk, node_id)
    )
