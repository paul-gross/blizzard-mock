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

**Skeleton, by design** (bootstrap P4 item 4): the CLI *surface* — the verbs,
their `--help`, and the intended contract — is real and stable, but each verb
raises a clear "not implemented" message because the domain models it operates
on do not exist until the hub/runner are scaffolded (P5). It grows verb-by-verb
alongside those models.

## Build-step plug points

- `blizzard_mock.mock_data.cli:cli` — the click group. Fill the verb bodies as
  the hub/runner domain models land; keep the surface stable.
- Store access uses the pre-declared `sqlalchemy` + `psycopg` deps against the
  portable-SQL store surface (`bzh:sql-portable`); resolve the target store from
  `--store` + `DATABASE_URL` (or a sqlite path).
- The `fixture` verbs coordinate with `blizzard_mock.forge` and
  `blizzard_mock.fixture_workspace` so one named fixture mints all three state
  holders together.
- Owns test file `tests/test_mock_data_cli.py`.
