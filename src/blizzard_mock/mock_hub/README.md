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
| Host plane absent | `--no-host-plane` | `BZ_MOCK_HUB_NO_HOST_PLANE=1` | off |

A service-tier test points the real runner's `BZ_HUB_URL` at `http://{host}:{port}`.

## Hub API surface (the subset a runner calls)

Vendor-native paths + JSON, byte-compatible with the hub's wire models. Liveness stays
unauthenticated at `/api/*`; every runner route lives under `/api/fleet/*`, beside the one
operator route a runner's bootstrap calls (`POST /api/runners`) — this mock simulates only
runner-originating traffic, no board surface.

A host is who its bearer token says it is, as a runner is. Its token reaches `/api/fleet/host*` and nothing else: a runner's token on those five routes, and a host's on any other `/api/fleet` route, is refused **401**, and a retired host's token is refused **403**. A host is added in one tenant for life, and `X-Blizzard-Tenant` naming another is refused **400**. `POST /api/runners` takes an optional `host_id` that stands the runner under that host, refusing an unknown one **404** and a retired one **409**; the mock models no verb that retires, adopts or releases a host afterwards. A runner under a host presents its host's usage — the sample, miss and roster entry for the slug it registered, none if it registered none — in place of any report of its own.

A runner is who its bearer token says it is, as at the real hub. Adding a runner mints its
`rn_` id and token together; the token is the runner's only identity, and no request body
or query names the caller. Every `/api/fleet/*` route except the identity read resolves the
`Authorization: Bearer` token before its handler runs and refuses **401** when it is
missing, unknown, or revoked — tokenless calls are refused, never served. A runner added
but not yet registered is *never connected*: it may call in, but it holds no workspace
binding, its own-runner read answers **409**, and a claim from it is refused **403** like
an unregistered runner's. Registration only updates a runner already added; the name comes
from the body, and a blank one keeps the held name. Every received header is still
recorded (`GET /_captured`).

The hub holds a tenant registry. It starts with one tenant, `default`, under the pinned id
`ten_00000000000000000000000000`, and a scenario seeds more (`POST /_seed/tenants`). A runner
is added in exactly one tenant and serves it for life; registration and the identity read
both answer that tenant's `tenant_id` and its current `tenant_name`. `X-Blizzard-Tenant`
carries a tenant id, never a name, and is resolved as the real hub resolves it. On a fleet
call the runner token decides the tenant: a header naming any other tenant is refused **400**,
and a header naming the token's own tenant is accepted. The identity read resolves no tenant
and ignores the header. The operator routes act in the tenant the header names, answering
**404** `no such tenant` for an id no tenant holds. With no header they act in the hub's only
tenant, or answer **409** `{"code": "tenant_required"}` once it holds several. Questions, scopes and system
artifacts stay one shared set, and chunks reach a tenant only through their project, so a
scenario runs one tenant's work at a time.

Each tenant holds projects. The first tenant starts with one, `default` (named `Default`),
under the pinned id `proj_00000000000000000000000000`; a seeded tenant gets its own `default`
under a minted `proj_` id, and a scenario adds more (`POST /_seed/projects`). Every chunk
belongs to one project, the first tenant's `default` unless seeded otherwise. Every
chunk-owned view (`ChunkDetail`, `ChunkStatusView`, `QueuePeekEntry`, `FindingView`,
`GardenProposalView`) carries the chunk's `project_id` and the project's current
`project_slug` and `project_name`.

A runner serves the projects its declaration names. An entry names a project by id (a `proj_`
value) or by its current slug, and resolves only within the runner's own tenant; the mock keeps
no former slugs. Adding a runner with `projects` stores each entry beside the project it
resolved to, and refuses the add **422**, naming each entry no project answers to. Added with
no `projects`, the runner serves the tenant's only project, stored by id so a slug change never
touches it, or nothing once the tenant holds several. A registration's `projects` replaces the
declaration: `[]` serves nothing, and an omitted or `null` one keeps what the hub holds.
Registration doubles as the heartbeat, so it never refuses over the declaration: an entry no
project answers to is stored unresolved, serving nothing, and resolved afresh at the next
registration that declares it. Registration and the own-runner read answer the declaration as
`projects: [{entry, project_id, slug, name}]`, each project read at answer time so a rename
shows at once; an unresolved entry carries `project_id`, `slug` and `name` as `null`.

A registration may also send `bindings` — `[{project, workspace_id, agents}]` — and a `subscription` slug. Sent
bindings replace `projects` as the declaration, each binding's project in binding order (`[]` serves nothing). Both
are stored as sent on every registration and answered as `bindings: [{project, workspace_id, agents,
workspace_unreported}]`, `subscription` and `subscription_unrostered`, here and on the own-runner read. The flags are
read from the host's latest report when the runner stands under a host that has reported: `workspace_unreported`
when the report lacks the binding's workspace, `subscription_unrostered` when it lacks the slug. A runner with no
host, or whose host has never reported, is never flagged.

The matched peek narrows the ready order to the projects the caller serves before it scans
it, so another project's chunk is never its head and `hold` never stalls on one; an entry's
`position` is its place in that narrowed order. A project reached only through a flagged binding is left out, and
every project is under an unrostered subscription. The peek body's `project` keeps only the declaration entry it
names, by the entry or its resolved id, so an unserved or unresolved project is offered nothing. The unmatched `GET` peek is not narrowed. A
claim rechecks the declaration after the dependency check and before the capability check: a
chunk of a project the runner does not serve is refused with the incompatible **409**,
`reason: "project"` and `detail` `runner does not serve project '<slug>'`. A capability
refusal carries `reason: "capability"`.

| Method + path | Purpose |
|---------------|---------|
| `GET /api/health`, `GET /api/ready` | Liveness / readiness |
| `GET /api/fleet/queue/peek` | The ready queue (seeded, unclaimed chunks) — D-080 |
| `POST /api/fleet/queue/peek` | The matched fleet peek — at most one entry of a project the caller serves, capability- and policy-filtered for the caller |
| `POST /api/fleet/routes` | Claim a chunk → 201 route + first envelope; **403** `{chunk_id, runner_id, detail}` for a caller never connected or paused at the hub; **409** `{chunk_id, status, detail}` for an ended chunk (`detail` `chunk is terminal`) or one not `ready` (`detail` `chunk <id> is <status>, not ready to claim`), then a held route, an unmet prerequisite, or an incompatible runner (`{chunk_id, incompatible_runner_id, detail, reason}`, `reason` `project` or `capability`) |
| `POST /api/fleet/chunks/{id}/route-token` | Rotate the chunk's live route capability token; **404** with no live route, **409** when the route sits on an ended chunk |
| `GET /api/fleet/chunks/{id}` | Chunk detail — derived status, current node, route, escalation, questions |
| `GET /api/fleet/chunks/{id}/envelope` | The current node envelope, idempotent re-read (D-090); **409** once the chunk has ended (`done` or `stopped`) |
| `GET /api/fleet/chunks/{id}/work-items` | Pass-through work items — canned per pointer, no forge integration |
| `GET /api/fleet/chunks/{id}/garden/findings` | A worker-scoped read of the chunk's own routine's live finding bucket, or **404** for a chunk seeded with no `garden_run` |
| `GET /api/fleet/chunks/{id}/findings` | The findings the chunk's own accepted, minted garden proposal answers, or **404** for a chunk seeded with no `garden_answered_findings` |
| `GET /api/fleet/chunks/{id}/findings/{finding_id}` | One finding within the chunk's own answered set, or **404** for an id outside it |
| `POST /api/fleet/chunks/{id}/completions` | Apply a node-step completion — epoch-fenced (D-007); a `failure` outcome when the report is off the current node, its attempt escalated or holds an unanswered question of its own, or the node is hub-executed |
| `POST /api/fleet/chunks/{id}/decisions` | Runner-config gate → `parked_at_gate` (D-032); refused like a completion when the report is off the current node, escalated, or holds an open question |
| `POST /api/fleet/chunks/{id}/hub-advance` | Drive a chunk parked at a hub-executor node one step (#65/#66) |
| `POST /api/fleet/events` | Batched runner-fact push (full vocabulary, §Batched fact push below) |
| `POST /api/fleet/transcripts` | Batched transcript-segment push — retained by lease; a record whose `(chunk_id, epoch)` another runner minted is listed in `refused`, never stored |
| `GET /api/fleet/chunks/{id}/transcript-segments` | A lease's retained transcript, concatenated across every stored record |
| `POST /api/fleet/runners`, `GET /api/fleet/runners/{id}` | Register the caller (name, workspace, federation identity, `env_capacity`, `projects`, `bindings`, `subscription`, `agents`, `version`) → `{runner_id, runner_name, first_registration, tenant_id, tenant_name, projects, bindings, subscription, subscription_unrostered, hub_version, window}`, `hub_version` `0.2.0` and `window` `{floor: "0.2", ceiling: "0.2"}`; `version` is accepted and never judged, so only the `version_refused` lever refuses it **409** `{reason, runner_version, hub_version, floor, detail}`, recording nothing / read the caller's own mirrored `RunnerView` — both brakes (D-070/D-043) and its report; **403** for another runner's id — an id never added included — and **409** before the first registration |
| `GET /api/fleet/identity` | The caller's `{runner_id, runner_name, tenant_id, tenant_name}`, or **401** `{reason, runner_id}` with `reason` one of `missing`, `unknown`, `revoked`, `retired` (retired outranks revoked); registers nothing |
| `POST /api/runners` | Add a runner under a non-blank `name` in the request's tenant, serving the `projects` named → **201** `{runner_id, runner_name, token}`, the token returned once; **422** for a blank name or a `projects` entry no project of the tenant answers to, plus the tenant refusals above |
| `POST /api/hosts` | Add a host under a non-blank `name` in the request's tenant, `adopt` placing existing runners under it → **201** `{host_id, host_name, token, adopted}`, the token returned once; **409** for a name a live host of the tenant holds or an adopted runner under a host or retired, **404** for an adopted runner the tenant does not hold, **422** for a blank name; nothing is added on a refusal |
| `POST /api/fleet/host`, `GET /api/fleet/host` | Report the calling host (`name`, `url`, `redirect_uris`, `workspaces`, `subscriptions`, the last two replaced whole, and `version`, accepted and never judged) / read its own record → `{host_id, tenant_id, host_name, registered_at, last_seen_at, runners}`, `runners` holding the `{runner_id, runner_name}` of each runner under it that is not retired. A blank name, or one another live host of the tenant holds, keeps the held name |
| `POST /api/fleet/host/events` | Push the calling host's own facts → `{host_id, high_water, applied, already_applied, rejected}`, against a mark of the host's own that no runner's push moves. Only the two usage kinds (`external_subscription_usage.sampled` / `.missed`) are applied, each stored per slug on the host's row; any other kind, or a usage fact without a `slug` (a miss also needs a `reason`), is rejected and holds the mark behind it |
| `POST /api/fleet/host/runners` | Add a runner under the calling host, in the host's tenant → **201** `{runner_id, runner_name, token}`, the token returned once; **422** for a blank name or a `projects` entry no project answers to |
| `POST /api/fleet/host/runners/{id}/retire` | Retire a runner under the calling host, revoking its token → **200** `{runner, released_chunk_ids}` with `runner` holding `runner_id`, `runner_name`, `retired`, `retired_by` and `host_id` alone and `released_chunk_ids` always empty, since the mock holds no routes to refuse over or release; **404** for a runner not under the calling host |
| `GET /api/me` | The implicit operator of a hub without sign-in → `{user_id, username, display_name, role, permissions, assignable_roles, tenants, hub_admin}`, `tenants` holding every tenant ordered by id as `{tenant_id, name, role: "admin", permissions}`; `role` is the tenant `X-Blizzard-Tenant` names, else the only one's, else `null`; `permissions` lists are empty |
| `POST /api/runners/{id}/enrollments` | Rotate the runner's token → **201** with the new token; **404** for an id never added in the request's tenant, **409** for a retired runner |
| `GET /api/fleet/questions/{id}` | The runner's answer poll |
| `GET /api/fleet/scopes` | The deployment's scope vocabulary, newest first |

## Batched fact push (`POST /api/fleet/events`)

The full runner-fact vocabulary (`blizzard.wire.facts`), dispatched by `kind` and
partitioned into `applied`/`already_applied`/`rejected` against a per-runner
high-water mark — a replayed seq is re-acked, not re-applied, and an unrecognized
`kind` is rejected rather than silently applied:

| Kind | Effect |
|------|--------|
| `lease.minted` | Advances the fence (`chunk.latest_epoch`, D-044); the first runner to mint an epoch owns it |
| `escalation.recorded` | Records the escalation — readable via `ChunkDetail.escalation`; rejected on a stopped or done chunk or below the newest epoch |
| `question.asked` | Mints a pollable question — readable via `GET /questions/{id}` and `ChunkDetail.questions`; rejected on a stopped or done chunk or below the newest epoch |
| `answer.delivered` | Marks the named, answered question delivered; rejected for a question not yet answered or one belonging to another chunk; a repeat is applied and writes nothing |
| `runner.locally_paused` | Sets the runner's `locally_paused`/`_by`/`_reason` (runner-scoped) |
| `runner.locally_resumed` | Clears the runner's `locally_paused`/`_by`/`_reason` |
| `usage.recorded` | Accepted (no fence, no gate) — no per-node-step usage ledger modeled |
| `event.recorded` | Accepted (no fence, no gate) — no operational event log modeled |
| `external_subscription_usage.sampled` | Upserts the runner's newest sample for its `slug` — readable via `GET /runners/{id}` |
| `external_subscription_usage.missed` | Upserts the runner's newest reported miss for its `slug`, in its own store, never touching the sample row |

The four runner-scoped kinds — both `runner.locally_*` and the usage sample/miss pair —
are held per runner — the one the pushing token names — and applied whether or not it has
registered yet, so a report the outbound buffer replays ahead of its first registration is
readable once that registration lands. A usage sample or miss must carry a non-empty `slug`; its payload is
coerced at ingest (defaulted `sampled_at`/`missed_at`, unusable windows dropped), so an
accepted fact can never make a later read raise. Held per `(runner_id, slug)`, one
subscription's sample never overwrites a sibling's (nor does a miss overwrite a sample,
or vice versa).

A registration's own declared roster (`RunnerRegistration.declared_subscriptions`)
decides `RunnerView.subscriptions`' membership when one is declared: one view per
declared slug, whatever the age of its sample — a slug reported but no longer declared
is simply absent, its rows untouched. With no roster declared, membership falls back to
the reported-slug union it has always been. On both paths a surviving sample's fields
are never blanked: when the newest miss carries `credential_lapsed` and postdates the
newest (or absent) sample, `condition` is set alongside the sample's own fields if one
exists, or the slug renders as a miss-only row — `sampled_at: null`, `windows: []` — if
it has never sampled at all. A miss with any other reason, or one that predates the
newest sample, never surfaces as a `condition` — `RunnerView.subscriptions[].condition`
is `null` for every other slug. Unlike the real hub, this mock applies no staleness gate
to either a sample or a miss.

## Control plane

- `POST /_seed/chunk` — install a scripted chunk. `ChunkSpec` is the authoritative
  field list (work refs and graph-scoped artifacts included, both riding into the
  claim envelope alongside the node graph); notably, each node's
  `prompt`/`judgement_prompt` rides straight into the envelope, and `choices` names
  the `to` node. `garden_run` (`{routine_name, scope_slug}`) and `garden_findings`
  (each mirroring the real hub's `FindingView` fields, minus `routine_name`/
  `scope_slug`, which `garden_run` supplies) seed the chunk's own garden bucket —
  omitted, the chunk carries no run context, mirroring a chunk that is not a routine
  run. `status` seeds the chunk at a status with no live route — `ready` by default, or
  `not_ready`, `paused`, `needs_human`, `delivering`, `done`, or `stopped` for a chunk no
  runner may claim; `running` is refused, since only a claim reaches it.
  `garden_answered_findings` (each mirroring the real hub's `FindingView` fields in
  full, carrying its own `routine_name`/`scope_slug`) seeds the findings the chunk's own
  accepted, minted garden proposal answers — an independent lever from `garden_run`/
  `garden_findings`, since a minted chunk carries no run context at all; omitted, the
  chunk answers no such proposal. `project_id` names the chunk's project by id — the
  first tenant's `default` when omitted, **404** for a project the hub does not hold — and
  the seed answers `{chunk_id, graph_id, project_id}`. `POST /_seed/reset` clears all state, runners and their
  tokens included — as after a reset of a real hub's data, every token a runner holds then
  answers `unknown`, and `blizzard runner init` re-adds only with `--allow-readd`. The tenant
  registry returns to the first tenant alone, under its first name, holding its pinned
  `default` project alone.
- `POST /_seed/tenants {name, tenant_id?}` — create a tenant under a minted `ten_` id, or a
  pinned one (**409** for a pinned id already held), holding its own `default` project →
  **201** `{tenant_id, name}`. `PATCH
  /_seed/tenants/{id} {name}` renames it, as a hub administrator does, and its runners read
  the new name on their next registration (**404** for an id no tenant holds).
- `POST /_seed/runners {name, runner_id?, token?, tenant_id?, projects?}` — add a runner never
  connected, as `POST /api/runners` does, optionally under a pinned id, token, and tenant (the
  first tenant when omitted; **409** for a pinned id already added, **404** for an unknown
  tenant), serving `projects` resolved as that route resolves them (**422** for an entry no
  project of the tenant answers to). `POST /_seed/runners/{id}/retire {by}` retires it and revokes its token,
  so the identity read answers `retired`; `POST /_seed/runners/{id}/token-revocations`
  revokes the token alone (`revoked` until a rotation); `POST
  /_seed/runners/{id}/pause?paused=` sets or releases the fleet's brake on it. All three answer
  **404** for an id never added.
- `PUT /_seed/runners/{id}/projects {projects}` — replace the runner's served declaration as
  a registration declaring `projects` does, without registering it: an unresolved entry is
  kept, serving nothing. Answers the declaration as stored; **404** for an id never added.
- `POST /_seed/projects {slug, name, tenant_id?, project_id?}` — create a project in a tenant
  (the first tenant when omitted) under a minted `proj_` id, or a pinned one → **201**
  `{project_id, tenant_id, slug, name}`; **404** for an unknown tenant, **409** for a pinned
  id already held or a slug the tenant already holds.
- `POST /_seed/scopes {slug, description?, retired?, created_at?}` — upsert one scope
  in the global vocabulary; a scenario seeds the end state it wants
  directly rather than replaying create/retire. Seeding the same `slug` twice replaces
  it.
- `POST /_seed/answer {question_id, answer, answered_by?}` — test-control only, plays
  the operator's answer so a scenario can make the runner's poll return
  `answered=True` without a real operator surface (the fleet mirror carries no
  board-facing answer route).
- `POST /_seed/stop {chunk_id}` — test-control only, plays the operator's stop verb so
  a scenario can drive a seeded chunk to `stopped` (the fleet mirror carries no
  board-facing stop route). One write: status goes terminal and the live route
  releases together, so `GET /chunks/{id}` never serves a route alongside a stopped
  status; the chunk's seeded graph and escalation state are untouched.
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
| `conflicting_fact` | `{runner_id?}` | `GET /chunks/{id}` reports a route held by a *different* runner — `runner_id`, or a fixed `rn_` id when omitted — a conflicting locator fact |
| `unreachable` | `remaining?` | All requests → 503; `remaining=N` heals after N calls (go unreachable *mid-lease*) |
| `unreachable_transcripts` | `remaining?` | `POST /transcripts` alone → 503; every other route (incl. `/events`) stays healthy |
| `delay_transcripts` | `{ms}` | `POST /transcripts` alone sleeps `ms`; every other route (incl. `/events`) stays fast |
| `replay` | — | The next completion returns the *previous* apply-response replayed — a duplicate delivery, no re-advance |
| `stale_envelope` | — | `GET /chunks/{id}/envelope` stamps a stale (`latest_epoch-1`) fence, so a completion from it is fenced out (D-007) |
| `chunk_unknown` | — | `GET /chunks/{id}` and `GET /chunks/{id}/envelope` 404 as an unknown chunk — the runner's env-release trigger — without deleting the chunk's actual state |
| `dependency_unmet` | `{prerequisite_chunk_id}` | `POST /routes` denies the claim, naming `prerequisite_chunk_id` as the unmet dependency; `GET /queue/peek` marks the same chunk `blocked`, read without consuming — sticky until cleared |
| `host_plane_absent` | — | Every route under `/api/fleet/host` answers `404 {"detail": "Not Found"}` — a hub from before hosts; every other route, `POST /api/hosts` included, is unaffected. Sticky until cleared. The startup switch (`--no-host-plane`) does the same for the process's life, surviving `/_levers/reset` and `/_seed/reset` |
| `fleet_flags` | `{<flag>: bool, …}` | `GET /api/fleet/identity` answers the payload as its `fleet_flags` map; `{}` while unarmed. Global and sticky — arming again replaces the map, `DELETE /_levers/fleet_flags` clears it |
| `version_refused` | `{runner_id, reason?, hub_version?, floor?, detail?}` | `POST /api/fleet/runners` from `runner_id` alone answers **409** `RunnerVersionRefusal` — `reason` (default `too-old`), the runner's sent `version`, `hub_version` and `floor` (default the mock's own), and `detail` — before anything is recorded; every other runner registers as usual. Sticky until cleared |

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
the happy path and every lever (`blizzard-mock:unit-test`) — and
`tests/test_mock_hub_projects.py`, the projects a runner serves: the declaration, the
narrowed peek and the project claim refusal.
