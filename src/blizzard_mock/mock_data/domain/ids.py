"""Prefixed-ULID id minting for seeded rows.

The minting itself is :mod:`blizzard_mock.ids`'s, shared with the mock hub; this module
holds the seeders' id-prefix registry. The random tail is seedable, so ``--seed``
reproduces byte-identical ids.
"""

from __future__ import annotations

# Re-exported, so a seeder names one module for both the minting and the prefixes.
from blizzard_mock.ids import RUNNER_PREFIX as RUNNER_PREFIX
from blizzard_mock.ids import mint as mint
from blizzard_mock.ids import seeded_rng as seeded_rng
from blizzard_mock.ids import ulid as ulid

#: The runner every seeder attributes its rows to when none is named — fixed across invocations.
SEED_RUNNER_ID = "rn_01HK153X00KN8V0ED48ZHQNMA9"

# The id-prefix registry — kept in step by hand with the real one (no import),
# so a composer mints an id that looks native alongside a real one.
CHUNK_PREFIX = "ch"
GRAPH_PREFIX = "gr"
NODE_PREFIX = "nd"
CHOICE_PREFIX = "cho"
ARTIFACT_PREFIX = "art"
TRANSITION_PREFIX = "tr"
DECISION_PREFIX = "dec"
QUESTION_PREFIX = "qn"
LEASE_PREFIX = "lease"
SEGMENT_PREFIX = "seg"
TAKEOVER_PREFIX = "tko"
SELFTEST_PREFIX = "self"
HUB_EXEC_SLOT_PREFIX = "hes"
MIGRATION_PREFIX = "mg"
USER_PREFIX = "usr"
GARDEN_PROPOSAL_PREFIX = "gprop"
