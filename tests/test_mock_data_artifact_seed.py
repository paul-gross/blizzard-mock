"""Unit coverage for the artifact composer (``blizzard-mock:unit-test``).

Pure, no store: ``compose_artifact`` is a plain function over already-loaded
data (``bzh:domain-takes-objects``) — an ordinary ``FactRow`` composer, exactly
like every existing one.
"""

from __future__ import annotations

from datetime import UTC, datetime
from random import Random

import pytest

from blizzard_mock.clock import FixedClock
from blizzard_mock.mock_data.domain.hub.artifact_seed import (
    ArtifactCompositionError,
    compose_artifact,
    generate_asset_content,
)

_NOW = datetime(2026, 1, 1, tzinfo=UTC)


def _clock() -> FixedClock:
    return FixedClock(_NOW)


def test_compose_artifact_git_commit_lands_the_pinned_ref() -> None:
    row = compose_artifact(
        chunk_id="ch_1",
        node_id="nd_1",
        node_name="build",
        epoch=2,
        name="pr-branch",
        kind="git_commit",
        clock=_clock(),
        rng=Random(1),
        repo="acme/widget",
        branch="feature/x",
        commit="abc123",
    )
    assert row.table == "artifacts"
    assert row.values["chunk_id"] == "ch_1"
    assert row.values["node_id"] == "nd_1"
    assert row.values["node_name"] == "build"
    assert row.values["epoch"] == 2
    assert row.values["name"] == "pr-branch"
    assert row.values["kind"] == "git_commit"
    assert row.values["data"] == "feature/x:abc123"
    assert row.values["repo"] == "acme/widget"
    assert row.values["forge"] is None
    assert row.values["produced_at"] == _NOW
    assert str(row.values["artifact_id"]).startswith("art_")


def test_compose_artifact_git_commit_carries_an_optional_forge() -> None:
    row = compose_artifact(
        chunk_id="ch_1",
        node_id="nd_1",
        node_name="build",
        epoch=1,
        name="pr-branch",
        kind="git_commit",
        clock=_clock(),
        rng=Random(1),
        repo="acme/widget",
        branch="feature/x",
        commit="abc123",
        forge="github",
    )
    assert row.values["forge"] == "github"


def test_compose_artifact_git_commit_requires_repo_branch_and_commit() -> None:
    with pytest.raises(ArtifactCompositionError, match="requires --repo, --branch, and --commit"):
        compose_artifact(
            chunk_id="ch_1",
            node_id="nd_1",
            node_name="build",
            epoch=1,
            name="pr-branch",
            kind="git_commit",
            clock=_clock(),
            rng=Random(1),
            repo="acme/widget",
        )


def test_compose_artifact_git_commit_refuses_asset_payload_flags() -> None:
    with pytest.raises(ArtifactCompositionError, match="asset-only"):
        compose_artifact(
            chunk_id="ch_1",
            node_id="nd_1",
            node_name="build",
            epoch=1,
            name="pr-branch",
            kind="git_commit",
            clock=_clock(),
            rng=Random(1),
            repo="acme/widget",
            branch="feature/x",
            commit="abc123",
            content="oops",
        )


def test_compose_artifact_asset_lands_verbatim_content() -> None:
    row = compose_artifact(
        chunk_id="ch_1",
        node_id="nd_1",
        node_name="deliver",
        epoch=1,
        name="review-notes",
        kind="asset",
        clock=_clock(),
        rng=Random(1),
        content="findings: looks good",
    )
    assert row.values["kind"] == "asset"
    assert row.values["data"] == "findings: looks good"
    assert row.values["repo"] is None
    assert row.values["forge"] is None


def test_compose_artifact_asset_generates_content_of_the_requested_size() -> None:
    row = compose_artifact(
        chunk_id="ch_1",
        node_id="nd_1",
        node_name="deliver",
        epoch=1,
        name="big-log",
        kind="asset",
        clock=_clock(),
        rng=Random(7),
        content_size=256,
    )
    assert len(str(row.values["data"])) == 256


def test_compose_artifact_asset_requires_exactly_one_content_source() -> None:
    with pytest.raises(ArtifactCompositionError, match="exactly one"):
        compose_artifact(
            chunk_id="ch_1",
            node_id="nd_1",
            node_name="deliver",
            epoch=1,
            name="review-notes",
            kind="asset",
            clock=_clock(),
            rng=Random(1),
        )
    with pytest.raises(ArtifactCompositionError, match="exactly one"):
        compose_artifact(
            chunk_id="ch_1",
            node_id="nd_1",
            node_name="deliver",
            epoch=1,
            name="review-notes",
            kind="asset",
            clock=_clock(),
            rng=Random(1),
            content="a",
            content_size=1,
        )


def test_compose_artifact_asset_refuses_git_commit_payload_flags() -> None:
    with pytest.raises(ArtifactCompositionError, match="git_commit-only"):
        compose_artifact(
            chunk_id="ch_1",
            node_id="nd_1",
            node_name="deliver",
            epoch=1,
            name="review-notes",
            kind="asset",
            clock=_clock(),
            rng=Random(1),
            content="findings",
            repo="acme/widget",
        )


def test_compose_artifact_refuses_an_unknown_kind() -> None:
    with pytest.raises(ArtifactCompositionError, match="unknown artifact kind"):
        compose_artifact(
            chunk_id="ch_1",
            node_id="nd_1",
            node_name="build",
            epoch=1,
            name="x",
            kind="bogus",
            clock=_clock(),
            rng=Random(1),
        )


def test_generate_asset_content_is_reproducible_under_the_same_seed() -> None:
    assert generate_asset_content(64, Random(42)) == generate_asset_content(64, Random(42))


def test_generate_asset_content_differs_across_seeds() -> None:
    assert generate_asset_content(64, Random(1)) != generate_asset_content(64, Random(2))
