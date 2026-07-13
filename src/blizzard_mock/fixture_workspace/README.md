# `blizzard_mock.fixture_workspace` — fixture-workspace scaffold

## Contract

A scaffold tool that **mints a real, disposable winter workspace** for the
runner-under-test to act on. Mocking the workspace seam would gut verification —
winter is the reference binding and acquire / release / reset-on-acquire is
exactly the behavior that must be *seen* working — so the fixture workspace is
**generated but real**:

- A directory of **bare git origin repos**, addressed as `file://` remotes (no
  network, no real forge in the git path).
- A **real winter workspace** initialized against them, with a small committed
  history and a `.winter/config.toml` declaring them as project repos. It is a
  real workspace root: the runner drives the real winter CLI against *it*.
- Lives under a **per-env scratch path** keyed off the feature env (e.g.
  `WINTER_ENV`), so two envs verifying at once never share a fixture.

**One git truth.** The bare origins this mints are the **same repos the mock
forge fronts** (`blizzard_mock.forge`) — so a mock harness's real commit pushes
to a `file://` origin, the forge mints a PR against that same repo, and the
merge is a real merge into bare `main`.

## Binary

`blizzard-mock-fixture` → `blizzard_mock.fixture_workspace.cli:main`. Scaffolds
(and tears down) a fixture workspace under the per-env scratch path.

## Build-step plug points

- `blizzard_mock.fixture_workspace.cli:main` — the entrypoint. Currently a usage
  stub; grows a `create` / `destroy` (and `path`) surface keyed off `WINTER_ENV`.
- The bare-origin minting, the `winter ws init` invocation, the committed-seed
  history, and the `.winter/config.toml` generation live under this package.
- Coordinate the bare-repo directory layout with `blizzard_mock.forge` (shared
  git truth) and the scenario seeding with `blizzard_mock.mock_data` (a named
  fixture mints workspace git state + forge state + store rows together).
- Owns test file `tests/test_fixture_workspace_smoke.py`.
