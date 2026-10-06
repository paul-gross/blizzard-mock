"""Prefixed-ULID minting shared by every mock component.

Id format: `blizzard/src/blizzard/foundation/ids.py` — re-implemented independently,
no ``blizzard`` import: ``<prefix>_<26 Crockford base32 chars>``. The random tail is drawn
from the caller's ``random.Random``, so a seeded one reproduces byte-identical ids.
"""

from __future__ import annotations

import random

from blizzard_mock.clock import Clock

_CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
_TIME_CHARS = 10  # 48 bits of millisecond timestamp
_RAND_CHARS = 16  # 80 bits of randomness

#: A runner's hub-minted id prefix — kept in step by hand with the real ``IdPrefix.RUNNER``.
RUNNER_PREFIX = "rn"


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
