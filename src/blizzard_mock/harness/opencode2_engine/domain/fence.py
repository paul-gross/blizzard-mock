"""The fence a turn's script must pass before it runs: the same two factors the exec engine's fence holds every other
mock harness to, since executing caller-supplied code is the feature and must never pass for a real OpenCode."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

#: Kept equal to ``blizzard_mock.harness.engine``'s constants, which this stdlib-only package cannot import.
FENCE_ENV_VAR = "BLIZZARD_MOCK_HARNESS_FENCE"
FENCE_ENV_VALUE = "1"
FENCE_MARKER_FILENAME = ".blizzard-mock-harness-fence"


def refusal(directory: Path, env: Mapping[str, str]) -> str | None:
    """Why a turn in ``directory`` under ``env`` may not run, or ``None`` when both factors mark it as test
    scaffolding: ``FENCE_ENV_VAR`` set to ``FENCE_ENV_VALUE``, and a marker file in ``directory`` or an ancestor."""
    if env.get(FENCE_ENV_VAR) != FENCE_ENV_VALUE:
        return f"mock OpenCode 2 refused to run a script: {FENCE_ENV_VAR}={FENCE_ENV_VALUE} is not set"
    resolved = directory.resolve()
    if not any((candidate / FENCE_MARKER_FILENAME).is_file() for candidate in (resolved, *resolved.parents)):
        return f"mock OpenCode 2 refused to run a script: no {FENCE_MARKER_FILENAME} in {resolved} or above"
    return None


__all__ = ["FENCE_ENV_VALUE", "FENCE_ENV_VAR", "FENCE_MARKER_FILENAME", "refusal"]
