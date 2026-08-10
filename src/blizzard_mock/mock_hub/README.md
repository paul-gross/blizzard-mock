# `blizzard_mock.mock_hub` — mock hub

## Contract

A standalone, **stateful** HTTP service that stands in for the **subset of the
blizzard hub API a runner consumes** (`blizzard.runner.loop.hub.IHubClient`), so the
**runner** can be built and service-tested against it (`implementation/mocking.md`,
"the runner → run it against the mock hub"). It mirrors the real hub OpenAPI subset
**without importing `blizzard`** — exactly as the mock forge mirrors GitHub REST
without importing octokit.

**Stateful.** It advances a *seeded, scripted* chunk through its node graph exactly
as the real hub would over the wire: a claim mints a route and hands back the first
envelope, a completion is **epoch-fenced** (D-007) and **idempotent** (D-090), a hub
(`deliver`) node "takes over" and the chunk derives `done`.

**Levered.** Every edge case a runner must survive is a lever an agent pulls by name
rather than contriving.

## Binary & configuration

`blizzard-mock-hub` → `blizzard_mock.mock_hub.cli:main` (a click command). Serves the
FastAPI app via uvicorn.

| Setting | Flag | Env | Default |
|---------|------|-----|---------|
| Bind host | `--host` | `BZ_MOCK_HUB_HOST` | `127.0.0.1` |
| Bind port | `--port` | `BZ_MOCK_HUB_PORT` | `8421` |

A service-tier test points the real runner's `BZ_HUB_URL` at `http://{host}:{port}`.

## Hub API surface (the subset a runner calls)

Vendor-native paths + JSON, byte-compatible with the hub's wire models. Liveness stays
unauthenticated at `/api/*`; every other route lives under `/api/fleet/*` — this mock
simulates only runner-originating traffic (no board/operator surface at all), so its
whole hub mirror sits under the fleet prefix. The mock stays warn-tolerant by
construction — no `require_runner_principal` check, a tokenless call is served exactly
like an enrolled one — but every received header is still recorded (`GET /_captured`)
so a test can assert a real runner presented its bearer token.

| Method + path | Purpose |
|---------------|---------|
| `GET /api/health`, `GET /api/ready` | Liveness / readiness |
| `GET /api/fleet/queue/peek` | The ready queue (seeded, unclaimed chunks) — D-080 |
| `POST /api/fleet/routes` | Claim a chunk → 201 route + first envelope, or **409** conflict |
| `POST /api/fleet/chunks/{id}/route-token` | Rotate the chunk's live route capability token (issue #84b) |
| `GET /api/fleet/chunks/{id}` | Chunk detail — derived status, current node, route, escalation, questions |
| `GET /api/fleet/chunks/{id}/envelope` | The current node envelope, idempotent re-read (D-090) |
| `GET /api/fleet/chunks/{id}/work-items` | Pass-through work items — canned per pointer, no forge integration |
| `POST /api/fleet/chunks/{id}/completions` | Apply a node-step completion — epoch-fenced (D-007) |
| `POST /api/fleet/chunks/{id}/decisions` | Runner-config gate → `parked_at_gate` (D-032) |
| `POST /api/fleet/chunks/{id}/leases` | Direct, non-buffered `lease.minted` report — advances the fence (D-044), 202 `{"chunk_id"}` |
| `POST /api/fleet/chunks/{id}/escalations` | Direct, non-buffered `escalation.recorded` report — readable via `ChunkDetail.escalation`, 202 `{"chunk_id"}` |
| `POST /api/fleet/chunks/{id}/hub-advance` | Drive a chunk parked at a hub-executor node one step (#65/#66) |
| `POST /api/fleet/events` | Batched runner-fact push (full vocabulary, §Batched fact push below) |
| `POST /api/fleet/runners`, `GET /api/fleet/runners/{id}` | Register (id, workspace, federation identity, `env_capacity`) / read the mirrored `RunnerView` — both brakes (D-070/D-043) and the newest usage sample |
| `GET /api/fleet/questions/{id}` | The runner's answer poll |

## Batched fact push (`POST /api/fleet/events`)

The full runner-fact vocabulary (`blizzard.wire.facts`), dispatched by `kind` and
partitioned into `applied`/`already_applied`/`rejected` against a per-runner
high-water mark — a replayed seq is re-acked, not re-applied, and an unrecognized
`kind` is rejected rather than silently applied:

| Kind | Effect |
|------|--------|
| `lease.minted` | Advances the fence (`chunk.latest_epoch`, D-044) |
| `escalation.recorded` | Records the escalation — readable via `ChunkDetail.escalation` |
| `question.asked` | Mints a pollable question — readable via `GET /questions/{id}` and `ChunkDetail.questions` |
| `answer.delivered` | Marks the named question answered |
| `runner.locally_paused` | Sets the runner's `locally_paused`/`_by`/`_reason` (runner-scoped) |
| `runner.locally_resumed` | Clears the runner's `locally_paused`/`_by`/`_reason` |
| `usage.recorded` | Accepted (no fence, no gate) — no per-node-step usage ledger modeled |
| `event.recorded` | Accepted (no fence, no gate) — no operational event log modeled (issue #125) |
| `external_subscription_usage.sampled` | Upserts the runner's newest sample — readable via `GET /runners/{id}` (issue #218) |

The three runner-scoped kinds — both `runner.locally_*` and the usage sample — are held
per `runner_id` and applied whether or not that runner has registered, so a report the
outbound buffer replays ahead of its registration is readable once that registration
lands. The usage payload is coerced at ingest (defaulted `sampled_at`, unusable windows
dropped), so an accepted fact can never make a later read raise.

## Control plane

- `POST /_seed/chunk` — install a scripted chunk (`ChunkSpec`: an `entry` node and a
  `nodes` map, each node's `prompt`/`judgement_prompt` riding straight into the
  envelope, `choices` naming the `to` node). `POST /_seed/reset` clears all state.
- `POST /_seed/answer {question_id, answer, answered_by?}` — test-control only, plays
  the operator's answer so a scenario can make the runner's poll return
  `answered=True` without a real operator surface (the fleet mirror carries no
  board-facing answer route).
- `GET /_levers` (catalog + active), `POST /_levers/{kind}` (arm), `DELETE
  /_levers/{kind}?chunk_id=` (clear), `POST /_levers/reset`.
- `GET /_captured` — the header-inspection lever: every fleet-facing
  `/api/*` request received so far (liveness — `/api/health`, `/api/ready` — excluded,
  same as the lever exemptions), `{requests: [{method, path, headers}, ...]}`, in
  arrival order. `POST /_captured/reset` clears it. A service test uses this to assert
  a real runner's
  outbound `Authorization` header actually reached the hub, on every runner→hub call
  including the work-items proxy forward.

## Lever surface (`/_levers`)

| Lever | Payload | Effect |
|-------|---------|--------|
| `delay` | `{ms}` | Sleep `ms` before answering (delay a response) |
| `drop_ack` | — | Apply the completion's write, then answer 503 — the ack is dropped though the transition landed; the re-flush is idempotent (D-090) |
| `conflicting_fact` | `{runner_id}` | `GET /chunks/{id}` reports a route held by a *different* runner — a conflicting locator fact |
| `unreachable` | `remaining?` | All requests → 503; `remaining=N` heals after N calls (go unreachable *mid-lease*) |
| `unreachable_transcripts` | `remaining?` | `POST /transcripts` alone → 503; every other route (incl. `/events`) stays healthy (D6) |
| `delay_transcripts` | `{ms}` | `POST /transcripts` alone sleeps `ms`; every other route (incl. `/events`) stays fast (D6) |
| `replay` | — | The next completion returns the *previous* apply-response replayed — a duplicate delivery, no re-advance |
| `stale_envelope` | — | `GET /chunks/{id}/envelope` stamps a stale (`latest_epoch-1`) fence, so a completion from it is fenced out (D-007) |
| `chunk_unknown` | — | `GET /chunks/{id}` and `GET /chunks/{id}/envelope` 404 as an unknown chunk — the runner's env-release trigger — without deleting the chunk's actual state |

Every lever is optionally scoped to one `chunk_id` and may self-expire after
`remaining` affected requests. The control plane and liveness are exempt from the
transport-edge levers so a test can always steer and gate on startup.

## Architecture

- `domain/` — dependency-free core (`bzh:domain-core`): the seed vocabulary
  (`ChunkSpec`/`NodeSpec`), the state machine (`ChunkState`), the wire-mirror response
  models, the lever vocabulary, the received-request capture store, and `MockHubService`
  (the rules).
- `internal/` — the in-memory state store (`bzh:dependency-inversion`).
- `api/` — the hub-mirror routes + the `/_seed`, `/_levers`, and `/_captured` control
  routers + the transport-edge lever middleware and the request-capture middleware
  (controllers hold only the service, `bzh:controller-read-only`).
- `app.py` — the composition root (`bzh:dependency-injection`).

The lever store and clock are the package-shared `blizzard_mock.levers` /
`blizzard_mock.clock`. Owns `tests/test_mock_hub.py` — unit + component coverage of
the happy path and every lever (`blizzard-mock:unit-test`).
