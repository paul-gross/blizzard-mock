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

Vendor-native paths + JSON, byte-compatible with the hub's wire models.

| Method + path | Purpose |
|---------------|---------|
| `GET /api/health`, `GET /api/ready` | Liveness / readiness |
| `GET /api/queue/peek` | The ready queue (seeded, unclaimed chunks) — D-080 |
| `POST /api/routes` | Claim a chunk → 201 route + first envelope, or **409** conflict |
| `GET /api/chunks/{id}` | Chunk detail — derived status, current node, route |
| `GET /api/chunks/{id}/envelope` | The current node envelope, idempotent re-read (D-090) |
| `POST /api/chunks/{id}/completions` | Apply a node-step completion — epoch-fenced (D-007) |
| `POST /api/chunks/{id}/decisions` | Runner-config gate → `parked_at_gate` (D-032) |
| `POST /api/events` | Batched runner-fact push; `lease.minted` advances the fence (D-044) |
| `POST /api/runners`, `GET /api/runners/{id}` | Register / read the pause brake (D-070/D-043) |

## Control plane

- `POST /_seed/chunk` — install a scripted chunk (`ChunkSpec`: an `entry` node and a
  `nodes` map, each node's `prompt`/`judgement_prompt` riding straight into the
  envelope, `choices` naming the `to` node). `POST /_seed/reset` clears all state.
- `GET /_levers` (catalog + active), `POST /_levers/{kind}` (arm), `DELETE
  /_levers/{kind}?chunk_id=` (clear), `POST /_levers/reset`.

## Lever surface (`/_levers`)

| Lever | Payload | Effect |
|-------|---------|--------|
| `delay` | `{ms}` | Sleep `ms` before answering (delay a response) |
| `drop_ack` | — | Apply the completion's write, then answer 503 — the ack is dropped though the transition landed; the re-flush is idempotent (D-090) |
| `conflicting_fact` | `{runner_id}` | `GET /chunks/{id}` reports a route held by a *different* runner — a conflicting locator fact |
| `unreachable` | `remaining?` | All requests → 503; `remaining=N` heals after N calls (go unreachable *mid-lease*) |
| `replay` | — | The next completion returns the *previous* apply-response replayed — a duplicate delivery, no re-advance |
| `stale_envelope` | — | `GET /chunks/{id}/envelope` stamps a stale (`latest_epoch-1`) fence, so a completion from it is fenced out (D-007) |

Every lever is optionally scoped to one `chunk_id` and may self-expire after
`remaining` affected requests. The control plane and liveness are exempt from the
transport-edge levers so a test can always steer and gate on startup.

## Architecture

- `domain/` — dependency-free core (`bzh:domain-core`): the seed vocabulary
  (`ChunkSpec`/`NodeSpec`), the state machine (`ChunkState`), the wire-mirror response
  models, the lever vocabulary, and `MockHubService` (the rules).
- `internal/` — the in-memory state store (`bzh:dependency-inversion`).
- `api/` — the hub-mirror routes + the `/_seed` and `/_levers` control routers +
  the transport-edge lever middleware (controllers hold only the service,
  `bzh:controller-read-only`).
- `app.py` — the composition root (`bzh:dependency-injection`).

The lever store and clock are the package-shared `blizzard_mock.levers` /
`blizzard_mock.clock`. Owns `tests/test_mock_hub.py` — unit + component coverage of
the happy path and every lever (`blizzard-mock:unit-test`).
