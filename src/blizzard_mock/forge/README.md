# `blizzard_mock.forge` — mock GitHub forge

## Contract

A standalone HTTP service that mocks the **subset of the GitHub API blizzard
touches**, plus browser destinations for delivery links, covering **two seams
with one vendor surface**:

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
| `GET /repos/{o}/{r}/issues?state=&labels=` | List issues; `labels=` is a comma-separated AND filter, composes with `state=all` |
| `GET /repos/{o}/{r}/issues/{n}` | Get issue (body) |
| `POST /repos/{o}/{r}/issues` | Create issue |
| `GET /repos/{o}/{r}/issues/{n}/comments` | List the comment thread |
| `POST /repos/{o}/{r}/issues/{n}/comments` | Add a comment (issue or PR) |
| `GET /repos/{o}/{r}/labels` | List repo-level label definitions |
| `POST /repos/{o}/{r}/labels` | Create a repo-level label; 422 if the name already exists |
| `GET /repos/{o}/{r}/issues/{n}/labels` | List an issue's labels |
| `POST /repos/{o}/{r}/issues/{n}/labels` | Add label(s) to an issue (idempotent) |
| `DELETE /repos/{o}/{r}/issues/{n}/labels/{name}` | Remove one label from an issue; 404 if not set |
| `GET /repos/{o}/{r}/pulls?state=&base=&head=&sort=&direction=&page=&per_page=` | List PRs. `head` is `<owner>:<branch>`; `sort` is `created` (default) or `updated`; `direction` defaults to `desc` for `created`/no sort, `asc` otherwise, ties by number; `per_page` caps at 100 and a paged answer carries a `Link` header (`next`/`last`, `prev`/`first`) |
| `POST /repos/{o}/{r}/pulls` | Create PR (`head`/`base` branches); 422 if an open PR already exists for the same `head`/`base` |
| `GET /repos/{o}/{r}/pulls/{n}` | Get PR — live `mergeable`/`mergeable_state`, `merged` |
| `PATCH /repos/{o}/{r}/pulls/{n}` | Close a PR without merge (D-065 terminal) |
| `PUT /repos/{o}/{r}/pulls/{n}/merge` | Real merge into `base` by `merge_method` — a merge commit, one squash commit on the base tip, or a rebase that always lands new commits; 405 conflict, 409 stale-sha |
| `PUT /repos/{o}/{r}/pulls/{n}/update-branch` | Merge `base` into head (advances `head.sha`), clears `stale_branch` → 202; 409 stale `expected_head_sha` |
| `GET /repos/{o}/{r}/pulls/{n}/merge` | Merged-check → 204 / 404 |
| `GET /repos/{o}/{r}/commits/{ref}` | Resolve a commit |
| `POST /repos/{o}/{r}/check-runs/{id}/rerequest` | Acknowledge a check-run re-request with `201 {}` — runs are derived live, so nothing is reset |
| `GET /repos/{o}/{r}/commits/{ref}/check-runs` | List check runs against `ref` — `{"total_count": N, "check_runs": [...]}`; the runs `ref_checks` armed against its revision, else derived live from the lever set |
| `GET /repos/{o}/{r}/compare/{base}...{head}` | Compare two refs (slashed branch names allowed) — `status` (`identical`/`ahead`/`behind`/`diverged`), `ahead_by`, `behind_by`, `merge_base_commit`, `commits` (`base..head`, oldest first, with `parents`), `files` (merge base → head diff: `filename`, `status`, `sha`, `additions`, `deletions`, `patch`, `previous_filename` on rename); 404 if either side does not resolve |
| `GET /repos/{o}/{r}/git/ref/{ref}` | Resolve a ref (e.g. `heads/main`) → sha |
| `POST /repos/{o}/{r}/git/refs` | Create a branch (`{"ref": "refs/heads/x", "sha"}`) → 201; 422 `Reference already exists` |
| `PATCH /repos/{o}/{r}/git/refs/{ref}` | Fast-forward a ref (`force` skips the check); 422 when not a fast-forward |
| `DELETE /repos/{o}/{r}/git/refs/heads/{b}` | Delete a branch → 204; 422 `Reference does not exist`. Open pulls headed or based on it close |
| `POST /repos/{o}/{r}/merges` | Merge branch `head` into `base` outside any PR → 201 with the merge commit (first parent is the base), 204 when the base already holds the head, 409 on conflict, 404 when a side is missing |
| `GET /healthz` | Liveness |

## Conditional reads and rate headers

Over the `/repos/` surface only (`/_levers`, `/healthz`, the docs routes, and the
browser pages carry neither):

- Every `200` `GET` answers a strong `ETag`, a hash of its body, so it changes
  exactly when the answer does. A matching `If-None-Match` answers `304` with that
  `ETag` and the rate headers — and spends nothing.
- Every other answer that reaches a route spends one request from its credential's
  window and carries `X-RateLimit-Limit`, `-Remaining`, `-Reset` (epoch seconds), and
  `-Resource: core`. The credential is the one in `Authorization` (`token X` or
  `Bearer X`); a request with none shares one anonymous bucket. A window opens at a
  bucket's first counted answer and restores to the limit at its reset, an hour on.
- Counting down never refuses. Only `rate_limited` and `secondary_rate_limited` do,
  and a lever refusal is answered before anything is counted.

## Browser destinations

The `html_url` on a PR and commit resolves to a small browser-readable page on
this same forge: `GET /{owner}/{repo}/pull/{number}` and
`GET /{owner}/{repo}/commit/{sha}`. Both look up the actual PR or bare-repo
commit; an unknown reference returns 404. The page displays the repository,
PR disposition or commit message, and the landed SHA when a PR has merged.

## Lever surface (`/_levers`)

`GET /_levers` (catalog + active), `POST /_levers/{kind}` (arm / fire),
`DELETE /_levers/{kind}?repo=&number=&branch=&pattern=` (clear), `POST /_levers/reset`
(which also clears the armed check runs and the rate windows). The
`/_levers` surface is exempt from the request-scoped levers so a test can always
clear one it armed.

| Lever | Shape | Effect |
|-------|-------|--------|
| `externally_merged` | action (repo, number, `method`) | Real merge of head→base outside the PR flow by `method` (`merge` by default, `squash`, or `rebase` — each lands a revision that is neither the head nor, for squash and rebase, a merge of it); marks PR `merged_by: external` — the direct push a polling delivery flow must detect (D-065) |
| `comment_midflight` | action (repo, number, body) | Appends a comment to a live thread (D-074) |
| `merge_conflict` | state (per PR) | `mergeable=false`/`dirty`; merge → 405 |
| `merge_rejected` | state (per PR) | Merge → 405 with an optional `message` (branch policy) |
| `stale_branch` | state (per PR) | `mergeable_state=behind` — base moved, no conflict; `PUT .../update-branch` clears it and advances the head → `clean` (the self-heal path) |
| `checks_pending` | state (per PR) | `mergeable_state=blocked` — content-mergeable but required checks/reviews not green yet; cleared to stand in for "CI went green" |
| `checks_failed` | state (per PR) | One `completed`/`failure` check run on the PR head; also forces `mergeable_state=blocked` — an armed PR is never green with a red check |
| `base_checks_failed` | state (per repo) | One `completed`/`failure` check run read against the revision the default branch resolves to — "the base gate was already red", independent of any PR |
| `ref_checks` | action (repo, ref, checks) | Arms named runs (`{name, status}`, status `pending`, `failed`, or `success`) against the revision `ref` resolves to. A run of an existing name replaces it, a new name adds a run beside the others, an empty list arms "no runs". Armed runs are answered as they are; any other revision keeps the derived run |
| `branch_protected` | state (repo, branch) | `PATCH git/refs` on the branch → 422 `Protected branch update failed…`. Scoped by branch, so several coexist; clear with `?branch=` |
| `ruleset` | state (repo, pattern) | `POST git/refs` and `POST /merges` (by base) on a branch matching the glob → 422 `Repository rule violations found…`. Scoped by pattern; clear with `?pattern=` |
| `merge_commits_disallowed` | state (per repo) | `PUT .../merge` with the `merge` method → 405 `Merge commits are not allowed on this repository`; `squash` and `rebase` are admitted |
| `rate_budget` | action (token?, remaining?, reset?) | Sets a token's rate window — `reset` in epoch seconds, as the header; with no `token`, every bucket, present and future |
| `rate_limited` | state (global/repo) | 403 + `X-RateLimit-*` headers, `Remaining: 0` and the bucket's window `Reset`; optional `remaining` self-expiry |
| `secondary_rate_limited` | state (global/repo) | 403 whose message names a secondary rate limit, with `Retry-After` and the bucket's live `Remaining` untouched; optional `remaining` self-expiry |
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
- **A listing that names no `per_page` is unpaged.** Every match is page 1 and later
  pages are empty, rather than GitHub's thirty, so a client that never names it (a
  supported release's `land_common.py`) still sees every pull. With no `sort`, a
  listing is newest first, as GitHub's is.
- **A closed pull freezes its head.** Closing, merging, or deleting the head branch pins
  the pull's `head.sha`, so a pull whose branch is gone still lists; deleting a branch
  closes the open pulls it heads or is based on.
- **Every merge method rewrites.** A rebase always lands new commits, even when the head
  already descends from the base, and a squash is one commit whose only parent is the
  base tip.
- **The default rate limit is 5000**, GitHub's own, held in `DEFAULT_RATE_LIMIT`. Hub
  traffic carries one token, so the counted `/repos/` requests in a suite's forge logs
  bound what that token spends in an hour; measured when the forge grew them, the full
  crash sweep totalled 446 across its seven workers' forges, and the journey 47 up to
  its takeover step — both far under 5000, so the limit stays GitHub's.
- **Merges run in a throwaway linked worktree** with the forge's own committer
  identity, so the bare repo needs no configured identity.

## Architecture

- `domain/` — dependency-free core (`bzh:domain-core`): models, errors, levers,
  the git/state/lever/check-run/rate-ledger/clock Protocol seams, and `ForgeService`
  (the rules, including every refusal).
- `internal/` — package-private adapters (`bzh:dependency-inversion`): the
  GitPython git backend, in-memory state, lever, check-run and rate-ledger stores,
  git-error factory.
- `api/` — GitHub-shaped routers (controllers hold only `ForgeService`,
  `bzh:controller-read-only`) + serialization + the request-lever middleware and the conditional/rate middleware
  inside it.
- `app.py` — the composition root wiring it all (`bzh:dependency-injection`).

Owns `tests/test_forge_smoke.py`, `tests/test_forge_refs.py`,
`tests/test_forge_listing.py`, `tests/test_forge_levers_policies.py`, and
`tests/test_forge_rate.py` — unit + component coverage (`blizzard-mock:unit-test`),
real bare repos in tmp dirs.
