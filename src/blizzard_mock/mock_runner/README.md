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
  | `POST /_drive/register` | `POST {hub}/api/runners` — join the fleet |
  | `POST /_drive/peek` | `GET {hub}/api/queue/peek` |
  | `POST /_drive/claim` `{chunk_id}` | `POST {hub}/api/routes`; on success records the held lease and reports `lease.minted` (advances the hub's fence, D-044) |
  | `POST /_drive/complete` `{chunk_id, choice}` | Submits the held node-step's epoch-fenced completion; advances the held lease on `next` |
  | `POST /_drive/get-chunk` `{chunk_id}` | `GET {hub}/api/chunks/{id}` |
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

## Architecture

- `domain/` — dependency-free core (`bzh:domain-core`): the `IHubGateway` seam, the held
  lease + drive bodies, the lever vocabulary, and `MockRunnerService` (the driving rules).
- `internal/` — the httpx binding of the gateway (`bzh:dependency-inversion`).
- `api/` — the runner-mirror + `/_drive` + `/_levers` routers (`bzh:controller-read-only`).
- `app.py` — the composition root (`bzh:dependency-injection`), which owns the
  `httpx.Client` to the hub (tests inject a gateway over an in-process hub instead).

Owns `tests/test_mock_runner.py` — the driver against an in-process mock hub, happy path
+ every lever (`blizzard-mock:unit-test`).
