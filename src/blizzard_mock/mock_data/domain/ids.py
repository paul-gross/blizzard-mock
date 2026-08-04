"""Prefixed-ULID id minting for seeded rows.

Every hub-entity id blizzard mints is a prefixed ULID (``blizzard.foundation.ids``):
a short type tag, an underscore, then a Crockford-base32 ULID whose leading 48
bits are the mint instant, so a plain string sort is a chronological sort. This
module re-implements the same encoding independently — no ``blizzard`` import,
per the mock-data contract's first property — so an id this tool mints looks
native next to a real one.

The one deliberate difference: the ULID's random tail is drawn from an
**injected, seedable** ``random.Random`` rather than ``os.urandom`` — a later
phase's ``--seed`` CLI flag then mints byte-identical ids on every run, so a
whole seeded scenario is reproducible. The mint instant still comes from the
injected :class:`~blizzard_mock.clock.Clock` (``bzh:injected-clock``), never
the wall clock directly.
"""

from __future__ import annotations

import random

from blizzard_mock.clock import Clock

_CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
_TIME_CHARS = 10  # 48 bits of millisecond timestamp
_RAND_CHARS = 16  # 80 bits of randomness
_ULID_CHARS = _TIME_CHARS + _RAND_CHARS

# The id-prefix registry — kept in step by hand with ``blizzard.foundation.ids``
# (no import: this tool never imports ``blizzard``), so a composer mints an id
# that looks native alongside a real one.
CHUNK_PREFIX = "ch"
GRAPH_PREFIX = "gr"
NODE_PREFIX = "nd"
CHOICE_PREFIX = "cho"
ARTIFACT_PREFIX = "art"
TRANSITION_PREFIX = "tr"
DECISION_PREFIX = "dec"
QUESTION_PREFIX = "qn"
LEASE_PREFIX = "lease"
TAKEOVER_PREFIX = "tko"
SELFTEST_PREFIX = "self"
HUB_EXEC_SLOT_PREFIX = "hes"
MIGRATION_PREFIX = "mg"
USER_PREFIX = "usr"


def seeded_rng(seed: int | None) -> random.Random:
    """A ``random.Random`` seeded for reproducible minting, or system-random when ``seed`` is ``None``."""
    return random.Random(seed)


def _encode(value: int, length: int) -> str:
    chars = []
    for _ in range(length):
        value, rem = divmod(value, 32)
        chars.append(_CROCKFORD[rem])
    return "".join(reversed(chars))


def ulid(clock: Clock, rng: random.Random) -> str:
    """A bare 26-char Crockford-base32 ULID stamped from ``clock``, randomized by ``rng``."""
    millis = int(clock.now().timestamp() * 1000)
    randomness = rng.getrandbits(_RAND_CHARS * 5)
    return _encode(millis, _TIME_CHARS) + _encode(randomness, _RAND_CHARS)


def mint(prefix: str, clock: Clock, rng: random.Random) -> str:
    """Mint a prefixed ULID — ``<prefix>_<ulid>``."""
    return f"{prefix}_{ulid(clock, rng)}"
