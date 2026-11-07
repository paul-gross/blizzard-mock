"""The mock hub's lever vocabulary — the edge states a runner-under-test must survive.

Realises the shared lever menu (``implementation/mocking.md``) on the hub's
side of the wire, plus ``chunk_unknown``. Store/arm/clear
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
    #: ``POST /transcripts`` alone answers 503 — every other route stays healthy: the
    #: lane-independence lever.
    UNREACHABLE_TRANSCRIPTS = "unreachable_transcripts"
    #: ``POST /transcripts`` alone sleeps ``payload.ms`` — the "slow, not hard-down"
    #: half the generic ``delay`` lever can't isolate.
    DELAY_TRANSCRIPTS = "delay_transcripts"
    #: The next completion's apply-response is the *previous* one replayed — a duplicate
    #: delivery the runner must absorb without double-acting.
    REPLAY = "replay"
    #: ``GET /chunks/{id}/envelope`` stamps a **stale** (``latest_epoch - 1``) fence, so a
    #: completion built from it is rejected as a zombie (D-007) — stale-envelope handling.
    STALE_ENVELOPE = "stale_envelope"
    #: ``GET /chunks/{id}`` and ``.../envelope`` report a genuine 404 without
    #: deleting the chunk's seeded state (commit ``68238d0``).
    CHUNK_UNKNOWN = "chunk_unknown"
    #: ``POST /routes`` denies the claim, naming ``payload.prerequisite_chunk_id`` as the
    #: unmet dependency — sticky until cleared, mirroring the real hub's
    #: standing edge rather than a one-shot fact.
    DEPENDENCY_UNMET = "dependency_unmet"
    #: Every route under ``/api/fleet/host`` answers a route-missing 404 — a hub from before
    #: hosts existed. Every other route, ``POST /api/hosts`` included, is unaffected.
    HOST_PLANE_ABSENT = "host_plane_absent"
    #: ``GET /api/fleet/identity`` answers ``payload`` as its ``fleet_flags`` — the hub's ``fleet`` flags a runner
    #: applies on a matched answer. Global and sticky; arming again replaces the map.
    FLEET_FLAGS = "fleet_flags"
    #: ``POST /api/fleet/chunks/{id}/artifact-check`` answers ``payload.refusal`` as its ``refusal`` — the hub
    #: refusing every staging offer with a fixed message. Sticky until cleared; unarmed, no offer is refused.
    ARTIFACT_REFUSAL = "artifact_refusal"
    IDP_OFFERED = "idp_offered"
    VERSION_REFUSED = "version_refused"
    LEGACY_CLAIM_REFUSALS = "legacy_claim_refusals"
    LEGACY_IDENTITY_REFUSALS = "legacy_identity_refusals"
    LEGACY_VERSION_REFUSALS = "legacy_version_refusals"
    LEGACY_REKEY_REFUSALS = "legacy_rekey_refusals"
    LEGACY_READ_REFUSALS = "legacy_read_refusals"
    LEGACY_HOST_RUNNER_REFUSALS = "legacy_host_runner_refusals"
    LEGACY_AUTH_REFUSALS = "legacy_auth_refusals"
    CLAIM_RUNNER_RETIRED = "claim_runner_retired"
    CLAIM_RUNNER_VERSION_REFUSED = "claim_runner_version_refused"
    PINNED_GRAPH_MISSING = "pinned_graph_missing"
    ENFORCE_RELEASE_ROUTE_TOKEN = "enforce_release_route_token"
    RUNNER_READ_MISSING = "runner_read_missing"
    HOST_ADD_RUNNER_LIFECYCLE_CONFLICT = "host_add_runner_lifecycle_conflict"
    HOST_RETIRE_LIFECYCLE_CONFLICT = "host_retire_lifecycle_conflict"


CATALOG: dict[str, str] = {
    HubLever.DELAY.value: "delay a response by payload.ms milliseconds",
    HubLever.DROP_ACK.value: "apply the completion but drop the ack (503); re-apply is idempotent (D-090)",
    HubLever.CONFLICTING_FACT.value: "GET chunk reports a route held by payload.runner_id (conflicting fact)",
    HubLever.UNREACHABLE.value: "all requests 503; remaining=N heals after N calls (unreachable mid-lease)",
    HubLever.UNREACHABLE_TRANSCRIPTS.value: (
        "POST /transcripts alone 503s; every other route (incl. /events) stays healthy (D6)"
    ),
    HubLever.DELAY_TRANSCRIPTS.value: (
        "POST /transcripts alone sleeps payload.ms; every other route (incl. /events) stays fast (D6)"
    ),
    HubLever.REPLAY.value: "re-emit the previous completion apply-response (duplicate delivery)",
    HubLever.STALE_ENVELOPE.value: "GET envelope stamps a stale (latest_epoch-1) fence (D-007)",
    HubLever.CHUNK_UNKNOWN.value: "GET chunk/envelope 404s as an unknown chunk — the runner's env-release trigger",
    HubLever.DEPENDENCY_UNMET.value: (
        "POST /routes denies the claim citing payload.prerequisite_chunk_id as the unmet dependency (sticky)"
    ),
    HubLever.HOST_PLANE_ABSENT.value: (
        "every /api/fleet/host route 404s as a missing route — a hub from before hosts (sticky)"
    ),
    HubLever.IDP_OFFERED.value: 'GET /api/auth/jwks.json answers 200 {"keys": []}; absent (404) while unarmed (sticky)',
    HubLever.FLEET_FLAGS.value: "GET /api/fleet/identity answers payload as its fleet_flags map (sticky, global)",
    HubLever.ARTIFACT_REFUSAL.value: (
        "POST /api/fleet/chunks/{id}/artifact-check answers payload.refusal as its refusal (sticky)"
    ),
    HubLever.VERSION_REFUSED.value: (
        "POST /api/fleet/runners from payload.runner_id answers 409 RunnerVersionRefusal from the payload (sticky)"
    ),
    HubLever.LEGACY_CLAIM_REFUSALS.value: "serve claim refusals without problem members using application/json",
    HubLever.LEGACY_IDENTITY_REFUSALS.value: "serve identity refusals without problem members using application/json",
    HubLever.LEGACY_VERSION_REFUSALS.value: "serve registration version refusals without problem members",
    HubLever.LEGACY_REKEY_REFUSALS.value: "serve route-token rekey refusals without problem members",
    HubLever.LEGACY_READ_REFUSALS.value: "serve resource read and release refusals without problem members",
    HubLever.LEGACY_HOST_RUNNER_REFUSALS.value: "serve host runner refusals without problem members",
    HubLever.LEGACY_AUTH_REFUSALS.value: "serve authentication and tenant refusals without problem members",
    HubLever.CLAIM_RUNNER_RETIRED.value: "refuse claims as if the runner were retired",
    HubLever.CLAIM_RUNNER_VERSION_REFUSED.value: "refuse claims as if the runner version were outside the window",
    HubLever.PINNED_GRAPH_MISSING.value: "graph-dependent calls answer 500 as if the chunk's pinned graph were missing",
    HubLever.ENFORCE_RELEASE_ROUTE_TOKEN.value: "enforce the live route token on release (sticky)",
    HubLever.RUNNER_READ_MISSING.value: "simulate the caller disappearing between authentication and runner read",
    HubLever.HOST_ADD_RUNNER_LIFECYCLE_CONFLICT.value: "simulate an adopted runner changing during host add",
    HubLever.HOST_RETIRE_LIFECYCLE_CONFLICT.value: "simulate a lifecycle race before or during host runner retirement",
}
