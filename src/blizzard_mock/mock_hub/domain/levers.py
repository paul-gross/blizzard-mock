"""The mock hub's lever vocabulary — the edge states a runner-under-test must survive.

Realises the shared lever menu (``implementation/mocking.md``) on the hub's
side of the wire, plus ``chunk_unknown`` (blizzard-mock#4). Store/arm/clear
semantics are the shared primitive (``blizzard_mock.levers``).
"""

from __future__ import annotations

from enum import StrEnum


class HubLever(StrEnum):
    """The named states the mock hub can be steered into."""

    #: Sleep ``payload.ms`` before answering a request (delay a response).
    DELAY = "delay"
    #: Apply a completion's write for real, then answer 503 — the ack is dropped though
    #: the transition landed; the runner re-flushes and the re-apply is idempotent (D-090).
    DROP_ACK = "drop_ack"
    #: ``GET /chunks/{id}`` reports a route held by a *different* runner
    #: (``payload.runner_id``) — a conflicting locator fact the runner detects.
    CONFLICTING_FACT = "conflicting_fact"
    #: Every request answers 503 (go unreachable). With ``remaining=N`` it heals after N
    #: affected calls — the "unreachable *mid-lease*, then recover" window.
    UNREACHABLE = "unreachable"
    #: ``POST /transcripts`` alone answers 503 — every other route, including ``/events``,
    #: stays healthy (D6, issue #246). The lane-independence lever: a wedged or slow
    #: transcript flush must never block the fact lane.
    UNREACHABLE_TRANSCRIPTS = "unreachable_transcripts"
    #: The next completion's apply-response is the *previous* one replayed — a duplicate
    #: delivery the runner must absorb without double-acting.
    REPLAY = "replay"
    #: ``GET /chunks/{id}/envelope`` stamps a **stale** (``latest_epoch - 1``) fence, so a
    #: completion built from it is rejected as a zombie (D-007) — stale-envelope handling.
    STALE_ENVELOPE = "stale_envelope"
    #: ``GET /chunks/{id}`` and ``.../envelope`` report a genuine 404 without
    #: deleting the chunk's seeded state (commit ``68238d0``).
    CHUNK_UNKNOWN = "chunk_unknown"


CATALOG: dict[str, str] = {
    HubLever.DELAY.value: "delay a response by payload.ms milliseconds",
    HubLever.DROP_ACK.value: "apply the completion but drop the ack (503); re-apply is idempotent (D-090)",
    HubLever.CONFLICTING_FACT.value: "GET chunk reports a route held by payload.runner_id (conflicting fact)",
    HubLever.UNREACHABLE.value: "all requests 503; remaining=N heals after N calls (unreachable mid-lease)",
    HubLever.UNREACHABLE_TRANSCRIPTS.value: (
        "POST /transcripts alone 503s; every other route (incl. /events) stays healthy (D6)"
    ),
    HubLever.REPLAY.value: "re-emit the previous completion apply-response (duplicate delivery)",
    HubLever.STALE_ENVELOPE.value: "GET envelope stamps a stale (latest_epoch-1) fence (D-007)",
    HubLever.CHUNK_UNKNOWN.value: "GET chunk/envelope 404s as an unknown chunk — the runner's env-release trigger",
}
