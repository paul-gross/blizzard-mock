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
verbs `reset`, `create`, and the `fixture` subgroup (`list`, `apply`).

## State of this component

**Two verbs live (P7W4), the rest stubbed.** The service tier needs to seed and
clean the real hub/runner stores, so the workhorse verbs are implemented; richer
domain rows seed through the daemons' own self-validating HTTP APIs, so the
mock-data `create` stays deliberately thin.

- `reset --store hub|runner --url <sqlite|dsn>` — **implemented**. Reflects the
  live schema and deletes every row from every table in FK-safe order (children
  before parents). Store-agnostic: it never imports `blizzard`, so it works
  against whatever the daemon's Alembic tree migrated. The workhorse — every
  service scenario starts from a clean store.
- `create runner --store hub --runner-id R [--paused] [--workspace-id W]` —
  **implemented**. Seeds one registered runner into the hub fleet registry
  (`runner_registrations`), and with `--paused` also lands a pause fact
  (`runner_pause_facts`) the runner reads back on its pull. Reflection-based.
- `create <other model>` and the `fixture` subgroup — **stubbed** (clean
  "not implemented" exit). Seed a chunk/graph/parked-question through the hub's
  own HTTP API in the service tier instead (self-validating).

## Design

- **No `blizzard` import.** The CLI reflects the target store's schema at runtime
  (SQLAlchemy `MetaData.reflect`), so a hub/runner schema change never forces a
  mock-repo edit — exactly as the forge mirrors GitHub without importing octokit.
- Store access uses the pre-declared `sqlalchemy` + `psycopg` deps against the
  portable-SQL store surface (`bzh:sql-portable`); resolve the target store from
  `--store` + `--url`/`$DATABASE_URL`.
- Owns test file `tests/test_mock_data_cli.py` (reset + create exercised against a
  real sqlite store whose schema mirrors the hub registry DDL).
