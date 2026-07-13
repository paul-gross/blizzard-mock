# `blizzard_mock.forge` — mock GitHub forge

## Contract

A standalone HTTP service that mocks the **subset of the GitHub API blizzard
touches**, covering **two seams with one vendor surface**:

- **Work-source seam** — issues, with bodies *and comment threads*, served
  **vendor-native** so the hub's GitHub-shaped pass-through reads (D-047 / D-074)
  are exercised against a GitHub-shaped surface.
- **Delivery seam** — PRs, merges, and the states the merge queue must survive
  (D-057 / D-058 / D-065).

**Backing model: a directory of bare git repos** — the *same* `file://` origins
the fixture-workspace (`blizzard_mock.fixture_workspace`) pushes to. This is the
single git truth: issue and PR metadata are mock state, but **mergeability is
computed against real refs**, **merging a PR performs a real merge into the bare
repo's `main`**, and the **external-merge lever is a direct push to `main`** that
a running delivery flow must then detect.

**Stateful and levered.** Explicit controls a test/agent pulls to steer the
forge into a named state instead of contriving it: externally-merged PR, merge
conflict, merge rejected, comment added mid-flight, rate-limited, token
rejected, unreachable.

Nothing off-the-shelf fits (surveyed July 2026, see `implementation/mocking.md`):
octokit mocks intercept at the JS client layer, octokit/fixtures is archived
record-replay, generic mock servers carry no GitHub domain state, and
Gitea/Forgejo are the wrong (GitHub-*like*, not GitHub-native) vendor surface.

## Binary

`blizzard-mock-forge` → `blizzard_mock.forge.cli:main`. Serves the FastAPI app
(uvicorn). Configuration selects the bare-repo directory and the bind port
(`BZ_FORGE_PORT` in the winter service band).

## Build-step plug points

- `blizzard_mock.forge.cli:main` — the entrypoint. Currently a usage stub.
- The FastAPI app, the GitHub-shaped routes, the bare-repo backing model, and
  the lever surface live under this package. Add an `internal/` subpackage for
  adapters per `bzh:dependency-inversion`; keep the domain (issue/PR/merge
  models, lever state) free of FastAPI (`bzh:domain-core`).
- Owns test file `tests/test_forge_smoke.py` — grow it into the forge's unit
  coverage (`blizzard-mock:unit-test`).
