# `blizzard_mock.mock_runner` — mock runner

## Contract

A standalone, **stateful** service that stands in for a runner so the **hub** can be
built and service-tested against it (`implementation/mocking.md`, "the hub → run it
against the mock runner"). The real runner is **outbound-only** — it calls the hub, the
hub never calls it — so the mock runner is not a passive server but a **controllable
driver**: it performs the runner's outbound protocol (register → peek → claim → complete)
against a real (or mock) hub, and its **levers** distort those outbound calls so the
hub-under-test meets each named misbehaviour over the wire.

It mirrors the real runner's outbound surface **without importing `blizzard`**.

## Binary & configuration

`blizzard-mock-runner` → `blizzard_mock.mock_runner.cli:main` (a click command).

| Setting | Flag | Env | Default |
|---------|------|-----|---------|
| Control-API host | `--host` | `BZ_MOCK_RUNNER_HOST` | `127.0.0.1` |
| Control-API port | `--port` | `BZ_MOCK_RUNNER_PORT` | `8431` |
| Hub to drive | `--hub-url` | `BZ_HUB_URL` | `http://127.0.0.1:8421` |
| Runner id | `--runner-id` | `BZ_MOCK_RUNNER_ID` | `runner-mock` |

## Surface

- **Runner-mirror** (its own served surface): `GET /api/health`, `GET /api/ready`.
- **Drive plane** — a test POSTs these and the driver performs the runner-role call
  against the hub, returning what it observed over the wire:

  | Route | Effect |
  |-------|--------|
  | `POST /_drive/register` | `POST {hub}/api/fleet/runners` — join the fleet |
  | `POST /_drive/peek` | `GET {hub}/api/fleet/queue/peek` |
  | `POST /_drive/claim` `{chunk_id}` | `POST {hub}/api/fleet/routes`; on success records the held lease and reports `lease.minted` (advances the hub's fence, D-044) via the dedicated `POST {hub}/api/fleet/chunks/{id}/leases` route by default — arm `lease_via_events` to route the same report through the batched `/events` push instead |
  | `POST /_drive/complete` `{chunk_id, choice, artifacts?}` | Submits the held node-step's epoch-fenced completion; advances the held lease on `next`. `artifacts` (optional, default `[]`) are the submission's `produces:` artifacts (`SubmittedArtifact` dicts — `{name, kind, content, attached}`), letting a service test drive the hub's `produces_mode=enforce` backstop (issue #113) over the wire |
  | `POST /_drive/get-chunk` `{chunk_id}` | `GET {hub}/api/fleet/chunks/{id}` |
  | `POST /_drive/escalate` `{chunk_id, takeover_command?, wrapped_takeover_command?}` | `POST {hub}/api/fleet/chunks/{id}/escalations` — reports retries-exhausted, fenced by the held lease's own epoch |
  | `POST /_drive/decide` `{chunk_id, choice?}` | `POST {hub}/api/fleet/chunks/{id}/decisions` — a runner-config gate decision; `choice` is cosmetic (not part of the wire submission) |
  | `POST /_drive/ask` `{chunk_id, question, options?}` | Pushes a `question.asked` fact via `POST {hub}/api/fleet/events`, minting a pollable question hub-side; returns the minted `question_id` |
  | `POST /_drive/poll-answer` `{question_id}` | `GET {hub}/api/fleet/questions/{id}` — the runner's answer poll |
  | `POST /_drive/pause` `{by?, reason?}` | Pushes a runner-scoped `runner.locally_paused` fact via `POST {hub}/api/fleet/events` (no `chunk_id`) |
  | `POST /_drive/resume` `{by?}` | Pushes a runner-scoped `runner.locally_resumed` fact via `POST {hub}/api/fleet/events` (no `chunk_id`) |
  | `POST /_drive/report-event` `{severity, kind, message, chunk_id?, lease_id?, node_name?, detail?}` | Pushes an `event.recorded` operational-event fact via `POST {hub}/api/fleet/events` (issue #125); `chunk_id` optional — a runner-scoped event names none |
  | `POST /_drive/reset` | Drop held leases + clear levers |

- **Levers** (`/_levers`): the same catalog/arm/clear/reset shape as the mock hub.

## Lever surface (`/_levers`)

| Lever | Effect on the hub-under-test |
|-------|------------------------------|
| `delay` `{ms}` | Sleep before the next outbound call |
| `drop_ack` | Submit the completion, then discard the hub's ack — the driver does not advance (the hub applied it, the ack was "lost") |
| `conflicting_fact` | Submit a completion naming the wrong `from_node_id` — a conflicting fact the hub rejects |
| `unreachable` | Claim then never complete — vanish *mid-lease*, leaving the hub a claimed-but-unfinished chunk to reap |
| `replay` | Submit the same completion twice — the hub must apply it once (epoch-idempotent, D-090) |
| `stale_epoch` | Submit a completion with a stale (held-epoch − 1) fence — the zombie the hub fences out over the wire (D-007) |
| `stale_route_token` | Submit a completion carrying a wrong route capability token — neither the held claim's own token nor any token the hub minted for this chunk — driving the hub's route-token check without a real runner |
| `omit_route_token` | Submit a completion carrying no route capability token at all — the pre-route-token-runner / dropped-field case `route_token_mode=warn` must absorb and `enforce` must reject |
| `lease_via_events` | Route the fence-advancing `lease.minted` report through the batched `/events` push instead of the dedicated `/chunks/{id}/leases` route — a transport-path selection, not a correctness distortion |

## Architecture

- `domain/` — dependency-free core (`bzh:domain-core`): the `IHubGateway` seam, the held
  lease + drive bodies, the lever vocabulary, and `MockRunnerService` (the driving rules).
- `internal/` — the httpx binding of the gateway (`bzh:dependency-inversion`).
- `api/` — the runner-mirror + `/_drive` + `/_levers` routers (`bzh:controller-read-only`).
- `app.py` — the composition root (`bzh:dependency-injection`), which owns the
  `httpx.Client` to the hub (tests inject a gateway over an in-process hub instead).

Owns `tests/test_mock_runner.py` — the driver against an in-process mock hub, happy path
+ every lever (`blizzard-mock:unit-test`).
