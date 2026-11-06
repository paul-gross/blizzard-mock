"""The runner selftest's responder: the selftest drives the real binding with prose prompts, not scripts, so a session
minted under the selftest's lease identity answers them as a compliant agent would — its first turn commits
``SELFTEST.txt``, and each later prose turn, the judgement among them, passes when that file is tracked. The key is the
identity, never the prompt's wording, and a tagged prompt is still the script it holds."""

from __future__ import annotations

from collections.abc import Mapping

from .program import tagged

#: The runner-injected lease identity, and the value the runner selftest pins on its spawn.
LEASE_ID_ENV_VAR = "BLIZZARD_LEASE_ID"
SELFTEST_LEASE_ID = "selftest"
SELFTEST_FILENAME = "SELFTEST.txt"

SPAWN_SCRIPT = f"""\
<behavior-script>
shell("printf 'ok\\\\n' > {SELFTEST_FILENAME} && git add {SELFTEST_FILENAME} && git -c user.email=mock@blizzard.local \
-c user.name='Mock Harness' commit -q -m 'selftest: trivial edit'")
text("Committed {SELFTEST_FILENAME}.")
</behavior-script>
"""
LATER_SCRIPT = f"""\
<behavior-script>
if shell("git ls-files --error-unmatch {SELFTEST_FILENAME}").exit_code == 0:
    text("<Choice>pass</Choice> {SELFTEST_FILENAME} is committed")
else:
    text("<Choice>fail</Choice> {SELFTEST_FILENAME} is not committed")
</behavior-script>
"""


def selftest_identity(environment: Mapping[str, str]) -> bool:
    return environment.get(LEASE_ID_ENV_VAR) == SELFTEST_LEASE_ID


def answer(prompt: str, *, first: bool) -> str | None:
    """The script a selftest session's ``prompt`` runs in place of its prose; ``None`` for a tagged prompt."""
    if tagged(prompt):
        return None
    return SPAWN_SCRIPT if first else LATER_SCRIPT


__all__ = ["LEASE_ID_ENV_VAR", "SELFTEST_FILENAME", "SELFTEST_LEASE_ID", "answer", "selftest_identity"]
