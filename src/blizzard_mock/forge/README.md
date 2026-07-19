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

## Binary & configuration

`blizzard-mock-forge` → `blizzard_mock.forge.cli:main` (a click command). Serves
the FastAPI app via uvicorn. Config is resolved from flags with env fallbacks:

| Setting | Flag | Env | Default |
|---------|------|-----|---------|
| Bare-repo directory | `--repos-dir` | `BZ_FORGE_REPOS_DIR` | *(required)* |
| Bind host | `--host` | `BZ_FORGE_HOST` | `127.0.0.1` |
| Bind port | `--port` | `BZ_FORGE_PORT` | `8080` |

The winter service band injects `BZ_FORGE_PORT` (band +1). `base_url` is derived
`http://{host}:{port}` and used to build vendor-native URLs in responses.

## GitHub REST v3 surface

Vendor-native paths and JSON; a GitHub-shaped client runs unmodified. Token auth
is honored — any token (or none) is accepted unless the `token_rejected` lever is
armed.

| Method + path | Purpose |
|---------------|---------|
| `GET /repos/{owner}/{repo}` | Repo, with `default_branch` read from bare `HEAD` |
| `GET /repos/{o}/{r}/issues?state=` | List issues |
| `GET /repos/{o}/{r}/issues/{n}` | Get issue (body) |
| `POST /repos/{o}/{r}/issues` | Create issue |
| `GET /repos/{o}/{r}/issues/{n}/comments` | List the comment thread |
| `POST /repos/{o}/{r}/issues/{n}/comments` | Add a comment (issue or PR) |
| `GET /repos/{o}/{r}/pulls?state=` | List PRs |
| `POST /repos/{o}/{r}/pulls` | Create PR (`head`/`base` branches) |
| `GET /repos/{o}/{r}/pulls/{n}` | Get PR — live `mergeable`/`mergeable_state`, `merged` |
| `PATCH /repos/{o}/{r}/pulls/{n}` | Close a PR without merge (D-065 terminal) |
| `PUT /repos/{o}/{r}/pulls/{n}/merge` | Real merge into `base`; 405 conflict, 409 stale-sha |
| `PUT /repos/{o}/{r}/pulls/{n}/update-branch` | Merge `base` into head (advances `head.sha`), clears `stale_branch` → 202; 409 stale `expected_head_sha` |
| `GET /repos/{o}/{r}/pulls/{n}/merge` | Merged-check → 204 / 404 |
| `GET /repos/{o}/{r}/commits/{ref}` | Resolve a commit |
| `GET /repos/{o}/{r}/git/ref/{ref}` | Resolve a ref (e.g. `heads/main`) → sha |
| `GET /healthz` | Liveness |

## Lever surface (`/_levers`)

`GET /_levers` (catalog + active), `POST /_levers/{kind}` (arm / fire),
`DELETE /_levers/{kind}?repo=&number=` (clear), `POST /_levers/reset`. The
`/_levers` surface is exempt from the request-scoped levers so a test can always
clear one it armed.

| Lever | Shape | Effect |
|-------|-------|--------|
| `externally_merged` | action (repo, number) | Real merge of head→base outside the PR flow; marks PR `merged_by: external` — the direct push a polling delivery flow must detect (D-065) |
| `comment_midflight` | action (repo, number, body) | Appends a comment to a live thread (D-074) |
| `merge_conflict` | state (per PR) | `mergeable=false`/`dirty`; merge → 405 |
| `merge_rejected` | state (per PR) | Merge → 405 with an optional `message` (branch policy) |
| `stale_branch` | state (per PR) | `mergeable_state=behind` — base moved, no conflict; `PUT .../update-branch` clears it and advances the head → `clean` (the self-heal path) |
| `checks_pending` | state (per PR) | `mergeable_state=blocked` — content-mergeable but required checks/reviews not green yet; cleared to stand in for "CI went green" |
| `rate_limited` | state (global/repo) | 403 + `X-RateLimit-*` headers; optional `remaining` self-expiry |
| `token_rejected` | state (global/repo) | 401 `Bad credentials` |
| `unreachable` | state (global/repo) | 503 |

## Recorded decisions (corpus was silent)

- **Metadata store is in-process, git is on-disk truth.** Issue/PR/comment
  metadata and merge dispositions live in memory beside the bare repos; refs and
  commits are the durable git truth. A fresh `winter service up` starts a fresh
  forge, so in-memory is the right lifetime.
- **Repo resolution is permissive** (the fixture-workspace scaffold owns the
  on-disk names): `{owner}/{repo}` resolves to the first valid git repo among
  `<dir>/owner/name(.git)`, `<dir>/name(.git)`, `<dir>/owner__name(.git)`.
- **Issue and PR numbers share one per-repo counter**, mirroring GitHub.
- **Merges run in a throwaway linked worktree** with the forge's own committer
  identity, so the bare repo needs no configured identity.

## Architecture

- `domain/` — dependency-free core (`bzh:domain-core`): models, errors, levers,
  the git/state/lever/clock Protocol seams, and `ForgeService` (the rules).
- `internal/` — package-private adapters (`bzh:dependency-inversion`): the
  GitPython git backend, in-memory state and lever stores, git-error factory.
- `api/` — GitHub-shaped routers (controllers hold only `ForgeService`,
  `bzh:controller-read-only`) + serialization + the request-lever middleware.
- `app.py` — the composition root wiring it all (`bzh:dependency-injection`).

Owns `tests/test_forge_smoke.py` — unit + component coverage
(`blizzard-mock:unit-test`), real bare repos in tmp dirs.
