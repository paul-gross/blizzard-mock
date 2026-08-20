"""Composes one ``artifacts`` row — a chunk's durable output at one node-step
(``bzh:facts-not-status``).

``kind`` discriminates the payload the way the real ``hub/domain/artifacts.py``
does, mirrored independently (no ``blizzard`` import).
"""

from __future__ import annotations

from random import Random

from blizzard_mock.clock import Clock
from blizzard_mock.mock_data.domain import ids
from blizzard_mock.mock_data.domain.facts import FactRow

GIT_COMMIT = "git_commit"
ASSET = "asset"

#: Every artifact kind ``artifacts.kind`` accepts.
KINDS = (GIT_COMMIT, ASSET)

#: The alphabet generated asset content draws from — plausible text, not binary.
_GENERATED_CONTENT_ALPHABET = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789 \n"


class ArtifactCompositionError(Exception):
    """A ``--kind``/payload combination :func:`compose_artifact` cannot honor."""


def generate_asset_content(size: int, rng: Random) -> str:
    """Filler content of exactly ``size`` characters, drawn from ``rng`` so
    ``--seed`` reproduces the same body byte-for-byte."""
    return "".join(rng.choice(_GENERATED_CONTENT_ALPHABET) for _ in range(size))


def compose_artifact(
    *,
    chunk_id: str,
    node_id: str,
    node_name: str,
    epoch: int,
    name: str,
    kind: str,
    clock: Clock,
    rng: Random,
    repo: str | None = None,
    forge: str | None = None,
    branch: str | None = None,
    commit: str | None = None,
    content: str | None = None,
    content_size: int | None = None,
) -> FactRow:
    """One ``artifacts`` row, minted fresh (``art_<ulid>`` — the wire decodes
    ``recorded_at`` from the id, never from ``produced_at``). ``kind`` picks
    which payload flags are consistent; an unknown kind or an inconsistent pair
    raises :class:`ArtifactCompositionError`.
    """
    if kind not in KINDS:
        raise ArtifactCompositionError(f"unknown artifact kind {kind!r} — one of {KINDS}")

    git_commit_fields_given = repo is not None or branch is not None or commit is not None or forge is not None
    asset_fields_given = content is not None or content_size is not None

    if kind == GIT_COMMIT:
        if asset_fields_given:
            raise ArtifactCompositionError(
                "--content/--content-size are asset-only — a git_commit's payload is --repo/--branch/--commit"
            )
        if repo is None or branch is None or commit is None:
            raise ArtifactCompositionError("git_commit requires --repo, --branch, and --commit")
        data = f"{branch}:{commit}"
        row_repo: str | None = repo
        row_forge = forge
    else:
        if git_commit_fields_given:
            raise ArtifactCompositionError(
                "--repo/--branch/--commit/--forge are git_commit-only — an asset's payload is --content/--content-size"
            )
        if (content is None) == (content_size is None):
            raise ArtifactCompositionError("asset requires exactly one of --content or --content-size")
        data = content if content is not None else generate_asset_content(content_size, rng)  # type: ignore[arg-type]
        row_repo = None
        row_forge = None

    artifact_id = ids.mint(ids.ARTIFACT_PREFIX, clock, rng)
    return FactRow(
        table="artifacts",
        values={
            "artifact_id": artifact_id,
            "chunk_id": chunk_id,
            "node_id": node_id,
            "node_name": node_name,
            "epoch": epoch,
            "name": name,
            "kind": kind,
            "data": data,
            "repo": row_repo,
            "forge": row_forge,
            "produced_at": clock.now(),
        },
    )
