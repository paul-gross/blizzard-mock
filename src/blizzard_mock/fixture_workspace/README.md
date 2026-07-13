# `blizzard_mock.fixture_workspace` — fixture-workspace scaffold

## Contract

A scaffold tool that **mints a real, disposable winter workspace** for the
runner-under-test to act on. Mocking the workspace seam would gut verification —
winter is the reference binding and acquire / release / reset-on-acquire is
exactly the behavior that must be *seen* working — so the fixture workspace is
**generated but real**:

- A directory of **bare git origin repos**, addressed as `file://` remotes (no
  network, no real forge in the git path), each with a small committed history.
- A **real winter workspace** initialized against them, with a generated
  `.winter/config.toml` declaring them as project repos. It is a real workspace
  root: the runner drives the real `winter` CLI against *it* (`winter ws init`,
  env creation, reset, commit, push).
- Lives under a **per-env scratch path** keyed off the feature env (`WINTER_ENV`),
  so two envs verifying at once never share a fixture.

**One git truth.** The bare origins this mints are the **same repos the mock
forge fronts** (`blizzard_mock.forge`) — so a mock harness's real commit pushes
to a `file://` origin, the forge mints a PR against that same repo, and the merge
is a real merge into bare `master`. The origins directory is exposed by
`blizzard-mock-fixture path --part origins` for exactly this wiring.

**No network — a local winter framework.** The winter framework the fixture runs
is cloned (`git clone --local`) from a **local winter source's committed master**
(a workspace whose `tools/winter-cli/` ships the CLI, e.g. the blizzard
workspace). Nothing is fetched from a remote forge.

## Scratch-path convention & layout

```
<scratch_root>/<env>/            root — the whole disposable fixture
├── origins/<repo>.git           bare git origins (the mock forge's git truth)
├── workspace/                   the REAL winter workspace root
│   ├── .winter/config.toml      declares the origins as file:// project repos
│   └── tools/winter-cli/        the winter framework (from the local source)
└── fixture.json                 provenance manifest (env, origins, source, repos)
```

`workspace/` holds both `.winter/config.toml` and `tools/winter-cli/`, so
winter-cli's cwd-walk-up root resolution lands on the **fixture**, never the outer
workspace the mock itself runs in — the isolation the runner-under-test needs.

Resolution precedence:

| Input | Flag | Env | Default |
|-------|------|-----|---------|
| env | `--env` | `$WINTER_ENV` | *(required)* |
| scratch root | `--scratch-root` | `$BLIZZARD_MOCK_SCRATCH_ROOT` | `<tmpdir>/blizzard-mock/fixtures` |
| winter source | `--winter-source` | `$BLIZZARD_MOCK_WINTER_SOURCE` | walk up from CWD for a winter workspace |

The two toy project repos minted into every fixture are defined in `seed.py`
(`toy-api`, `toy-web`).

## Binary & verbs

`blizzard-mock-fixture` → `blizzard_mock.fixture_workspace.cli:main` (a click group).

| Verb | Effect |
|------|--------|
| `mint` | Mint a fresh fixture (bare origins + seed history + local winter clone + `winter ws init`). Refuses if one already exists. Prints the workspace root. |
| `reset` | Re-mint from clean: destroy any existing fixture for the env, then mint. |
| `destroy` | Remove the fixture directory for the env. |
| `path [--part workspace\|origins\|root]` | Print a fixture path — the winter workspace root (default), the bare-origins dir (for the forge), or the fixture root. |

## Architecture

The domain core (`service.py`, `scratch.py`, `config.py`, `seed.py`) imports no
`subprocess` / `click`: git and the winter CLI are inverted behind the `IGit` and
`IWinterCli` Protocols (`bzh:dependency-inversion`), implemented under `internal/`
and injected at the CLI composition root (`bzh:dependency-injection`). Unit tests
substitute a fake winter CLI by type; the component test injects the real one.

## Coordinates with

- `blizzard_mock.forge` — point the forge at `path --part origins` (shared git truth).
- `blizzard_mock.mock_data` — a named scenario fixture mints workspace git state
  (here) alongside forge state and store rows for one consistent world.
- Owns test file `tests/test_fixture_workspace_smoke.py`.
