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
verbs `reset`, `create` (a group of its own: `runner`, `graph`, `chunk`, `usage`,
`lease`, `escalation`, `question`, `event`, `runner-pause`), `scenario` (a group
of its own: `board`), and the `fixture` subgroup (`list`, `apply`).

## State of this component

**Ten `create` verbs plus `scenario board` live (P7W4 + P2 + P3 + P4), `fixture`
still stubbed.** The service tier needs to seed and clean the real hub/runner
stores, so the workhorse verbs are implemented.

- `reset --store hub|runner --url <sqlite|dsn>` — **implemented**. Reflects the
  live schema and deletes every row from every table in FK-safe order (children
  before parents). Store-agnostic: it never imports `blizzard`, so it works
  against whatever the daemon's Alembic tree migrated. The workhorse — every
  service scenario starts from a clean store.
- `create runner --store hub --runner-id R [--paused] [--workspace-id W]` —
  **implemented**. Seeds one registered runner into the hub fleet registry
  (`runner_registrations`), and with `--paused` also lands a pause fact
  (`runner_pause_facts`) the runner reads back on its pull. Reflection-based.
- `create graph --store hub [--name NAME] [--seed N]` — **implemented**. Mints a
  synthetic workflow graph (`domain/graph_seed.py`): a `build` (`executor: runner`)
  node into a `deliver` (`executor: hub`) node into the reserved terminal. A
  freshly provisioned hub mints no graph of its own until first ingest, which is
  exactly why `create chunk` needs one of these. Prints the minted graph id.
- `create chunk --store hub --status <status> [--graph NAME] [--node NAME] [--work-ref SRC#REF]... [--runner-id R] [--epoch N] [--chunk-id ID] [--seed N]`
  — **implemented**, the root verb. Composes and writes the exact fact rows
  `blizzard.hub.domain.work.derive_chunk_status` reads to arrive at one of the nine
  derived statuses (`domain/chunk_seed.py`, `bzh:facts-not-status` — never a status
  column). Auto-mints a graph (`create graph`'s own logic) when the store holds
  none, or reuses one by `--graph NAME`. Prints the minted chunk id, alone, on
  stdout — pipeable into a sibling verb.
- `create usage --chunk ID --kind {spawn,resume,judge,nudge} --model M --input-tokens N [--output-tokens N] [--cache-read-tokens N] [--cache-create-tokens N] (--cost-usd X | --no-cost) [--node NAME] [--epoch N] [--runner-id R]`
  — **implemented**. Lands one `usage_facts` row (`domain/usage_seed.py`).
  `--no-cost` lands a genuine SQL `NULL` `cost_usd`, never a fabricated `0.0` — the
  hub's cost derivation reads a `NULL` row as a lower bound (`cost_partial`).
  `--node`/`--epoch`/`--runner-id` default from the chunk's own newest
  transition/lease when omitted.
- `create lease --chunk ID --runner-id R [--epoch N]` — **implemented**. Lands one
  `lease_facts` row (`domain/lease_seed.py`) — the same row shape `create chunk
  --status running/delivering` composes internally, which imports it rather than
  re-deriving the shape.
- `create escalation --chunk ID [--epoch N] [--takeover-command TEXT] [--cause {cap,retries}]`
  — **implemented**. Lands one `escalations` row (`domain/escalation_seed.py`);
  `needs_human` derives from an open one. `--cause cap` composes a takeover command
  carrying recognizable spend-cap wording (mirroring
  `_park_on_cost_cap`'s log-only reason string — never actually written to the real
  schema, since the real `takeover_command` column is cause-agnostic; see the
  module docstring); `--cause retries` (the default) composes the plain generic
  placeholder. `--takeover-command` overrides either default verbatim. Does **not**
  also write an `event_log` row — an open escalation is synthesized into the
  read-time event feed (`derive_event_feed`), so `create escalation` alone is
  sufficient for it to show up there.
- `create question --chunk ID --text T [--option TEXT]... [--answer A --answered-by W] [--delivered] [--resumed] [--node NAME] [--runner-id R] [--epoch N] [--seed N]`
  — **implemented**. Lands one open-or-answered `questions` trail
  (`domain/question_seed.py`); `waiting_on_human` derives from an open one.
  `--answer`/`--answered-by` (required together) also land a `question_answers`
  row; `--delivered` (requires `--answer`) also lands an `answer_deliveries` row.
  `--resumed` requires `--delivered` and lands no fact of its own — the real schema
  has no dedicated "resumed" row beyond the delivery. Each call mints its own
  question id, so a chunk can carry several independent trails. Prints the minted
  question id, alone, on stdout.
- `create event --kind K --severity {info,warning,critical} --message M [--chunk ID] [--runner-id R] [--node NAME] [--detail JSON]`
  — **implemented**. Lands one `event_log` row (`domain/event_seed.py`), the
  operational event feed. `--runner-id` (NOT NULL on the real table) defaults to
  `--chunk`'s newest lease's runner, or the `"mock-data"` placeholder absent
  either. `--detail` is opaque JSON, round-tripped only — validated to parse, never
  interpreted.
- `create runner-pause --runner-id R (--local | --fleet) [--reason TEXT]` —
  **implemented**. Lands one pause fact, engaged (`domain/runner_pause_seed.py`):
  `--local` on the runner's own brake (`runner_local_pause_facts`, `--reason`
  nullable there), `--fleet` on the fleet's brake (`runner_pause_facts`, which has
  **no** `reason` column — `--fleet --reason` fails loud naming the missing column
  rather than silently dropping it). Exactly one of `--local`/`--fleet` is
  required.
- `scenario board [--chunks N] [--stress] [--seed S] [--url ... | --dir ...]` —
  **implemented**. One command, one ready-to-view board (`domain/scenario_seed.py`),
  composed purely from the ten `create` verbs' own composers — no new fact shape
  of its own. Mints a graph, spreads `N` chunks (default 6) across the nine
  derived statuses by a fixed, deterministic algorithm (see the module
  docstring), lands a varying cost spread with at least one cost-partial usage
  fact, a ceiling-paused runner (`runner_local_pause_facts`, not a `--cause cap`
  escalation — the module docstring explains why), a runner per chunk, and a
  mixed-severity event log. `--stress` layers on four narrow-viewport/overflow
  extremes: a runner with a long identity, a chunk landed on a deliberately long
  custom node name, and a second `waiting_on_human` chunk carrying two extra
  independent question trails (multi-question). `--seed` seeds id-minting *and*
  pins the clock to a fixed instant (not just the RNG, unlike the `create`
  verbs), so two runs at the same seed compose byte-identical output — a
  property `scenario board` needs that a single-concept `create` verb does not.
  Always the hub store; prints the store it wrote to and a per-chunk, per-status
  summary census.
- The `fixture` subgroup — **stubbed** (clean "not implemented" exit). Named,
  versioned scenarios composing several concepts *and* stores (hub, forge, git)
  at once land in a later phase — `scenario` stays within the hub store alone.

## Design

- **No `blizzard` import.** The CLI reflects the target store's schema at runtime
  (SQLAlchemy `MetaData.reflect`), so a hub/runner schema change never forces a
  mock-repo edit — exactly as the forge mirrors GitHub without importing octokit.
  Each `domain/*_seed.py` composer independently mirrors the id-prefix/status/kind
  vocabulary it needs, the same precedent `domain/ids.py` set.
- Store access uses the pre-declared `sqlalchemy` + `psycopg` deps against the
  portable-SQL store surface (`bzh:sql-portable`); resolve the target store from
  `--store` + `--url`/`$DATABASE_URL`.
- The concept composers (one `domain/*_seed.py` module per concept) are pure
  functions of already-loaded data (`bzh:domain-takes-objects`) returning a
  `FactRow`/`list[FactRow]` — no SQLAlchemy, no store read of their own. `cli.py`
  (the composition root) does the reads a composer needs — `--graph NAME` reuse,
  and `create usage`/`create event`'s "derive from the chunk's own rows when a
  flag is omitted" lookups — via `ISeedStore.query`, then hands the composed rows
  to `SeedService.seed`.
- Owns test files `tests/test_mock_data_cli.py` (every implemented verb exercised
  against a real sqlite store whose schema mirrors the hub's own DDL), and one
  `tests/test_mock_data_<concept>_seed.py` per composer module (each composer's
  pure per-shape fact set, no store) — including
  `tests/test_mock_data_scenario_seed.py` for `domain/scenario_seed.py`, the one
  composer built purely on top of the others rather than against raw tables.
