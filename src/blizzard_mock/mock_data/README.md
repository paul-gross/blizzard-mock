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
verbs `reset`, `create` (a group of its own: `runner`, `graph`, `chunk`), and the
`fixture` subgroup (`list`, `apply`).

## State of this component

**Four verbs live (P7W4 + P2), `fixture` still stubbed.** The service tier needs to
seed and clean the real hub/runner stores, so the workhorse verbs are implemented.

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
- The `fixture` subgroup — **stubbed** (clean "not implemented" exit). Named,
  versioned scenarios composing several concepts at once land in a later phase.

## Design

- **No `blizzard` import.** The CLI reflects the target store's schema at runtime
  (SQLAlchemy `MetaData.reflect`), so a hub/runner schema change never forces a
  mock-repo edit — exactly as the forge mirrors GitHub without importing octokit.
  `domain/graph_seed.py`/`domain/chunk_seed.py` independently mirror the id-prefix
  and status vocabulary they need, the same precedent `domain/ids.py` set.
- Store access uses the pre-declared `sqlalchemy` + `psycopg` deps against the
  portable-SQL store surface (`bzh:sql-portable`); resolve the target store from
  `--store` + `--url`/`$DATABASE_URL`.
- The concept composers (`domain/graph_seed.py`, `domain/chunk_seed.py`) are pure
  functions of already-loaded data (`bzh:domain-takes-objects`) returning
  `list[FactRow]` — no SQLAlchemy, no store read of their own. `cli.py` (the
  composition root) does the one read a composer needs — `--graph NAME` reuse —
  via `ISeedStore.query`, then hands the composed rows to `SeedService.seed`.
- Owns test files `tests/test_mock_data_cli.py` (every implemented verb exercised
  against a real sqlite store whose schema mirrors the hub's own DDL),
  `tests/test_mock_data_graph_seed.py`, and `tests/test_mock_data_chunk_seed.py`
  (the composers' pure per-status/per-shape fact sets, no store).
