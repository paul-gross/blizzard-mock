# `blizzard_mock.mock_data` — mock-data CLI

## Contract

A CLI tool that **creates, destroys, or resets data** inside the data stores
(sqlite or postgres) of a hub or runner under test. It is a tool *for agents* to
set up test cases repeatably:

- It operates on **domain models, not raw tables** — an agent asks for "a chunk
  parked on a question," not for rows.
- Larger data sets are instantiated from **fixtures** — named, versioned
  scenarios the suite and agents share.
- **Reset** returns a store to a known-clean state, so every test run starts
  from the same ground.

A named fixture mints a **consistent world** across the three state holders that
must agree (`implementation/verification.md`): hub/runner store rows (this CLI),
forge state (`blizzard_mock.forge`), and git state
(`blizzard_mock.fixture_workspace`).

## Binary

`blizzard-mock-data` → `blizzard_mock.mock_data.cli:cli`. A click group with
verbs `reset`, `create` (a group of its own: `runner`, `graph`, `chunk`,
`artifact`, `usage`, `lease`, `escalation`, `question`, `event`,
`runner-pause`, `transcript-segment`), `scenario` (a group of its own:
`board`, `fleet`), and the `fixture` subgroup (`list`, `apply`).

## State of this component

**Eleven `create` verbs plus `scenario board`/`scenario fleet` live, `fixture`
still stubbed.** The service tier needs to seed and clean the real hub/runner
stores, so the workhorse verbs are implemented.

Every `create` verb also takes `--store hub|runner` (required — omitting it is
a `UsageError`; which store(s) serve a concept is `domain/store_targets.py`'s
own table, the single home of the refusal a store a concept doesn't live in
gives). `usage` and `lease` are the two concepts both stores carry today —
`--store runner` selects a different composer, whose flags differ
where the runner schema differs; a flag the selected store has no column for
is refused, naming it. `transcript-segment` has no hub counterpart at all.
Every verb except `scenario fleet` resolves its target store as
`--url <sqlite path|postgres dsn>` (or `$DATABASE_URL`), or as
`--dir <runtime dir>` — sugar that reads a hub/runner runtime's
`blizzard-hub.toml`/`blizzard-runner.toml` `db_url`
(`internal/runtime_config.py`) and resolves to the same `--url` before the
same code path runs; only `--url` is spelled out per verb below.
`scenario fleet` names two stores at once and so takes none of those three —
its own entry below owns its flags.

- `reset --store hub|runner --url <sqlite|dsn>` — **implemented**. Reflects the
  live schema and deletes every row from every domain table in FK-safe order
  (children before parents), skipping Alembic's own `alembic_version` bookkeeping
  table — deleting its row would un-stamp the store's migration state without
  dropping a single table, so the *next* daemon boot would replay every
  migration from scratch and die on the first `CREATE TABLE` that already
  exists. Store-agnostic: it never imports `blizzard`, so it works against
  whatever the daemon's Alembic tree migrated. The workhorse — every service
  scenario starts from a clean store.
- `create runner --store hub --runner-id R [--paused] [--workspace-id W]` —
  **implemented**. Seeds one registered runner into the hub fleet registry
  (`runner_registrations`), and with `--paused` also lands a pause fact
  (`runner_pause_facts`) the runner reads back on its pull. Reflection-based.
- `create graph --store hub [--name NAME] [--seed N]` — **implemented**. Mints a
  synthetic workflow graph (`domain/hub/graph_seed.py`): a `build` (`executor: runner`)
  node into a `deliver` (`executor: hub`) node into the reserved terminal. A
  freshly provisioned hub mints no graph of its own until first ingest, which is
  exactly why `create chunk` needs one of these. Prints the minted graph id.
- `create chunk --store hub --status <status> [--graph NAME] [--node NAME] [--work-ref SRC#REF]... [--runner-id R] [--epoch N] [--chunk-id ID] [--seed N]`
  — **implemented**, the root verb. Composes and writes the exact fact rows
  `blizzard.hub.domain.work.derive_chunk_status` reads to arrive at one of the nine
  derived statuses (`domain/hub/chunk_seed.py`, `bzh:facts-not-status` — never a status
  column). Auto-mints a graph (`create graph`'s own logic) when the store holds
  none, or reuses one by `--graph NAME`. `--status done` travels the graph's own
  edges — `build --approved--> deliver --landed--> the reserved terminal` —
  landing two transitions under one shared epoch, so the chunk carries two
  selectable node-steps rather than one; `--node` overrides which node the
  final, terminal-landing hop leaves from (default `deliver`), and naming
  `build` itself lands that hop alone — a self-edge is not one any minted graph
  carries. Prints the minted chunk id, alone, on stdout — pipeable into a
  sibling verb.
- `create artifact --store hub --chunk ID --name NAME --kind {git_commit,asset} [--repo REPO] [--forge FORGE] [--branch BRANCH] [--commit SHA] [--content TEXT | --content-size N] [--node NAME] [--epoch N] [--seed N]`
  — **implemented**. Lands one `artifacts` row (`domain/hub/artifact_seed.py`) — a peer
  of `lease_facts`/`usage_facts`, not an embedded column on `chunks`. `--kind`
  constrains the payload flags accepted: `git_commit` requires
  `--repo`/`--branch`/`--commit` (`--forge` optional) and composes the pinned
  `<branch>:<commit>` ref the chunk page's Artifacts tab reads; `asset` requires
  exactly one of `--content` (landed verbatim) or `--content-size` (a
  deterministic filler body of that many characters, drawn from the seeded RNG
  so `--seed` reproduces it byte-for-byte). Mixing a kind's payload flags with
  the other kind's is refused loud. `--node` is a graph **node name** — the
  `artifacts` row carries both the node's id and its name, and only the name is
  the one a reader types (`create chunk`'s own convention). Omitted,
  `--node`/`--epoch` default from the chunk's own newest transition/lease; a
  transition landing on the reserved terminal marker, which is no graph node,
  falls back to the node it left, so a `done` chunk defaults to `deliver` rather
  than refusing. The composition root does those reads, never the composer
  (`bzh:dependency-injection`). The minted id is a real `art_<ulid>` (the wire's
  `recorded_at` decodes from it, never from `produced_at`), so several calls
  against one chunk land distinct rows under whatever `(node, epoch)` pairs
  they're given. Prints the minted artifact id, alone, on stdout.
- `create usage --store {hub,runner} --chunk ID --kind {spawn,resume,judge,nudge} --model M --input-tokens N [--output-tokens N] [--cache-read-tokens N] [--cache-create-tokens N] (--cost-usd X | --no-cost) [--node ID] [--epoch N] [--runner-id R] [--lease-id ID] [--generation N]` —
  **implemented, store-polymorphic**. `--store hub` lands one `usage_facts` row
  (`domain/hub/usage_seed.py`): `--no-cost` lands a genuine SQL `NULL`
  `cost_usd`, never a fabricated `0.0` — the hub's cost derivation reads a `NULL` row
  as a lower bound (`cost_partial`) — and `--node`/`--epoch`/`--runner-id` default
  from the chunk's own newest transition/lease when omitted. `--store runner` lands
  one runner-store `usage_facts` row (`domain/runner/usage_seed.py`), keyed by
  `--lease-id`/`--generation` rather than `--runner-id` — the runner schema's own
  column set — so it requires `--node`/`--epoch`/`--lease-id` explicitly (no
  defaulting source exists against a runner store) and refuses `--runner-id`,
  naming the column; the hub side likewise refuses `--lease-id`/`--generation`.
- `create lease --store {hub,runner} --chunk ID --runner-id R [--epoch N]` —
  **implemented, store-polymorphic**. `--store hub` lands one `lease_facts` row
  (`domain/hub/lease_seed.py`) — the same row shape `create chunk
  --status running/delivering` composes internally. `--store runner` lands one
  `leases` row *plus* its `lease_context` sibling, always together
  (`domain/runner/lease_seed.py`, `[--node NAME] [--graph-id ID] [--retries-max N]
  [--seed N]`): the daemon's own lease reads (`list_active_leases`,
  `active_lease_for_chunk`, `latest_lease_for_chunk`, `list_closed_leases`, the
  escalation select) **inner-join** `lease_context`, so a bare `leases` row passes the
  drift guard but renders nowhere — this verb never composes one without the other.
  `pid`/`process_start_time`/`session_id` land `NULL`, the same as a real mint before
  spawn-return. The runner schema declares no foreign keys: a `--chunk` naming
  an id no hub store holds is accepted, not refused — a daemon wired to a hub that
  lacks that chunk, or that routes it elsewhere, abandons the lease on its next tick.
  The hub side refuses `--node`/`--graph-id`/`--retries-max`, naming the column, and
  `--seed` alongside them — the hub path mints no id for a seed to pin.
- `create escalation --store hub --chunk ID [--epoch N] [--takeover-command TEXT] [--wrapped-takeover-command TEXT] [--cause {cap,retries}]` — **implemented**. Lands one `escalations` row (`domain/hub/escalation_seed.py`); `needs_human` derives from an open one.
  `--cause cap` composes a `takeover_command` carrying recognizable spend-cap wording (mirroring `_park_on_cost_cap`'s log-only reason string — never actually written to the real schema, since the real `takeover_command` column is cause-agnostic; see the module docstring); `--cause retries` (the default) composes the plain generic placeholder.
  `--takeover-command` overrides either default verbatim.
  `wrapped_takeover_command`'s default (the `blizzard runner takeover` entry point) is a synthesized placeholder regardless of `--cause` — a real spend-cap park composes it the same way a retries-exhausted park does.
  `--wrapped-takeover-command` overrides that default verbatim, the same way `--takeover-command` overrides the raw one.
  Does **not** also write an `event_log` row — an open escalation is synthesized into the read-time event feed (`derive_event_feed`), so `create escalation` alone is sufficient for it to show up there.
- `create question --store hub --chunk ID --text T [--option TEXT]... [--answer A --answered-by W] [--delivered] [--resumed] [--node NAME] [--runner-id R] [--epoch N] [--seed N]`
  — **implemented**. Lands one open-or-answered `questions` trail
  (`domain/hub/question_seed.py`); `waiting_on_human` derives from an open one.
  `--answer`/`--answered-by` (required together) also land a `question_answers`
  row; `--delivered` (requires `--answer`) also lands an `answer_deliveries` row.
  `--resumed` requires `--delivered` and lands no fact of its own — the real schema
  has no dedicated "resumed" row beyond the delivery. Each call mints its own
  question id, so a chunk can carry several independent trails. Prints the minted
  question id, alone, on stdout.
- `create event --store hub --kind K --severity {info,warning,critical} --message M [--chunk ID] [--runner-id R] [--node NAME] [--detail JSON]`
  — **implemented**. Lands one `event_log` row (`domain/hub/event_seed.py`), the
  operational event feed. `--runner-id` (NOT NULL on the real table) defaults to
  `--chunk`'s newest lease's runner, or the `"mock-data"` placeholder absent
  either. `--detail` is opaque JSON, round-tripped only — validated to parse, never
  interpreted.
- `create runner-pause --store hub --runner-id R (--local | --fleet) [--reason TEXT]` —
  **implemented**. Lands one pause fact, engaged (`domain/hub/runner_pause_seed.py`):
  `--local` on the runner's own brake (`runner_local_pause_facts`, `--reason`
  nullable there), `--fleet` on the fleet's brake (`runner_pause_facts`, which has
  **no** `reason` column — `--fleet --reason` fails loud naming the missing column
  rather than silently dropping it). Exactly one of `--local`/`--fleet` is
  required.
- `create transcript-segment --store runner --chunk ID --node ID --lease-id ID --session-id ID [--epoch N] [--generation N] [--cursor TOKEN] [--shipped-bytes N] [--shipped-turns N] [--normalizer-version V] [--harness-version V] [--finalized] [--seed N]` —
  **implemented, runner-only**. Lands one `transcript_segments` row
  (`domain/runner/transcript_segment_seed.py`) — no hub counterpart exists.
  `--finalized` also stamps `finalized_at`; unset, the segment lands open. Prints
  the minted `seg_<ulid>` segment id, alone, on stdout.
- `scenario board [--chunks N] [--stress] [--seed S] [--url ... | --dir ...]` —
  **implemented**. One command, one ready-to-view board (`domain/hub/scenario_seed.py`),
  built on the hub-store `create` verbs' own composers for every per-concept row;
  only the `--stress` graph-node extension and the `runner_registrations` row are
  composed in that module itself. Mints a graph, spreads `N` chunks (default 6) across the nine
  derived statuses by a fixed, deterministic algorithm (see the module
  docstring), lands a varying cost spread with at least one cost-partial usage
  fact, an artifact spread (the `ready` chunk, an open `waiting_on_human`
  chunk, and the `done` chunk's own two node-steps each carry one, so a chunk
  detail page's Artifacts tab and its Node history tab's per-step artifact panel
  both render something on more than one chunk), a ceiling-paused runner (`runner_local_pause_facts`,
  not a `--cause cap` escalation — the module docstring explains why), a
  runner per chunk, and a mixed-severity event log. `--stress` layers on five
  deliberately extreme properties across three additional rows (see the module
  docstring): a runner with a long identity, a chunk landed on a deliberately
  long custom node name that also carries a deliberately long artifact name,
  and a second `waiting_on_human` chunk carrying two extra independent
  question trails (multi-question). `--seed` seeds id-minting *and* pins the
  clock to a fixed instant (`create graph`/`chunk`/`question` do the same
  under `--seed` — `cli.py`'s shared `_seeded_clock` helper), so two runs at
  the same seed compose byte-identical output. A `--seed`ed board's runners
  all render offline (`last_seen_at` pinned to the fixed instant, well outside
  any liveness window) and every timestamp reads as the fixed instant's date —
  fine for a reproducible demo, but not a "just seeded, live-looking" board.
  Always the hub store; prints the store it wrote to and a per-chunk, per-status
  summary census, including the artifact count.
- `scenario fleet [--chunks N] [--stress] [--seed S] (--hub-url ... | --hub-dir ...) (--runner-url ... | --runner-dir ...) [--runner-id ID]`
  — **implemented**. `scenario board` seeded into the hub store, then mirrored into
  the runner store under one pinned runner id — the same chunk ids, so the runner's
  own local panel renders leases, asks, escalations, takeovers, environments, and
  facts alongside the seeded board (`domain/runner/scenario_seed.py`); the mirrored
  `usage_facts` and `transcript_segments` are seeded for store-level coherence
  only — the local API exposes no panel surface for either. Composes exactly
  two runner-store shapes, both dormant: the `waiting_on_human` chunk mirrors as an
  active lease parked on an open ask (`NULL` `pid`/`session_id`); the `needs_human`
  chunk mirrors as a closed, escalated lease under an open takeover. No `running`
  chunk is mirrored — a runner-held running lease with no worker process is not a
  state a real fleet can hold, and the running daemon would reap it as a stalled
  attempt within one tick. The runner's own local pause lands engaged, so the
  mirrored fleet claims nothing off the board's `ready` queue. `--hub-url|--hub-dir`
  and `--runner-url|--runner-dir` are each **required explicitly** — neither falls
  back to `$DATABASE_URL`, since one env var cannot name two stores. `--runner-id`
  is required when only `--runner-url` names the runner store; given `--runner-dir`,
  the pinned id is read from its `blizzard-runner.toml` `runner_id` instead (the two
  are mutually exclusive, refused together). A `--chunks` too small for the runner
  half's mirror, or either store target that can't even be opened, is refused
  before either store is written. Writes the runner half's own local-pause brake
  first — before the hub half's `ready` chunks can land — then the hub half, then
  the rest of the runner half, through independent writes; any write failure names
  which half, if any, had already landed — and because the brake always lands
  first, every failure past that point names it as landed too, distinct from
  nothing having landed at all. `--seed` reproduces both halves
  byte-identically, sharing one `Clock`/`Random` pair across them. See
  `blizzard-context:/tooling/store-seeding.md`
  for the requirement this composes around — `--runner-dir` has no
  `blizzard-runner.toml` to resolve `db_url`/`runner_id` from until the runner
  runtime has been brought up at least once — and never clear the mirrored
  runner's local pause from the panel afterward.
- The `fixture` subgroup — **stubbed** (clean "not implemented" exit). Named,
  versioned scenarios composing several concepts *and* stores (hub, forge, git)
  at once land in a later phase — `scenario board` stays within the hub store
  alone; `scenario fleet` is the one that spans both.

## Design

- **No `blizzard` import.** The CLI reflects the target store's schema at runtime
  (SQLAlchemy `MetaData.reflect`), so a hub/runner schema change never forces a
  mock-repo edit — exactly as the forge mirrors GitHub without importing octokit.
  Each `domain/<store>/*_seed.py` composer independently mirrors the id-prefix/status/kind
  vocabulary it needs, the same precedent `domain/ids.py` set.
- Store access uses the pre-declared `sqlalchemy` + `psycopg` deps against the
  portable-SQL store surface (`bzh:sql-portable`); resolve the target store from
  `--store` plus the per-verb target flags above. Which store each concept is *allowed* is one
  table, `domain/store_targets.py`, that every verb routes its `--store` through —
  the single home of the refusal a hub-only concept gives `--store runner`, never a
  per-verb rewording.
- **The drift guard runs before any insert** (`domain/schema_contract.py`): every
  composed `FactRow` is checked, table by table, against the live store's
  *reflected* schema — the table exists, every supplied column key is a real
  column, and every reflected NOT-NULL/no-default/non-autoincrement-PK column is
  supplied. A miss is a schema drift — the live store moved out from under this
  tool — and raises `SchemaDriftError` naming the table and the offending
  column(s), never a silently-wrong row. Guide: `blizzard-context:/tooling/store-seeding.md`.
- **Referential integrity is enforced, not just column shape**: the drift guard above
  validates a `FactRow`'s shape, not whether an id it names (a `--chunk`/`--runner-id`
  this store never seeded) actually exists. `internal/reflected_store.py` turns on
  sqlite's `PRAGMA foreign_keys=ON` for the engine it writes through (sqlite leaves FK
  enforcement off per-connection by default, unlike postgres) and translates the
  underlying `IntegrityError` — a dangling foreign key, or a unique-constraint clash
  (e.g. re-running `scenario board` against a store that already carries one without an
  intervening `reset`) — into `SeedIntegrityError`, the same "fail loud" contract as a
  schema drift.
- The concept composers (one `domain/<store>/*_seed.py` module per concept, filed
  under `domain/hub/` or `domain/runner/` by the store its rows land in — the
  distinction a bare `runner_` prefix cannot carry, since the hub-store
  `runner_pause_seed.py` already spends it on the hub-side fleet concept) are pure
  functions of already-loaded data (`bzh:domain-takes-objects`) returning a
  `FactRow`/`list[FactRow]` — no SQLAlchemy, no store read of their own. `cli.py`
  (the composition root) does the reads a composer needs — `--graph NAME` reuse,
  and `create usage`/`create event`'s "derive from the chunk's own rows when a
  flag is omitted" lookups — via `ISeedStore.query`, then hands the composed rows
  to `SeedService.seed`.
- Owns test files `tests/test_mock_data_cli.py` (every implemented verb exercised
  against a real sqlite store whose schema mirrors the hub's own DDL, plus a
  runner-shaped one for the concepts that live there), and one
  `tests/test_mock_data_<concept>_seed.py` per hub composer module,
  `tests/test_mock_data_runner_<concept>_seed.py` per runner one (each composer's
  pure per-shape fact set, no store) — including the two scenario composers,
  `domain/hub/scenario_seed.py` and `domain/runner/scenario_seed.py`, which build
  on the other composers rather than against raw tables. The hub-side
  `runner_pause_seed.py` is the one name the `runner_` test prefix does not track:
  `tests/test_mock_data_runner_pause_seed.py` covers that **hub** composer, while
  `tests/test_mock_data_runner_local_pause_seed.py` covers the runner store's own.
