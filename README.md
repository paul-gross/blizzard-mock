# blizzard-mock

The **mock fleet** for [blizzard](https://github.com/paul-gross/blizzard). Everything
blizzard integrates with is a pluggable seam
(`blizzard-context:/architecture/system-shape.md`, `bzh:pluggable-seams`), and every
seam gets a controllable mock here.

The fleet exists for one reason: an agent building blizzard must be able to construct
any state — including the rare edge cases that matter most — **deterministically,
cheaply, with no real tokens and no network**. A mock here is never a stub that returns
a canned value; it is a real service over a real wire, with explicit **levers** that
steer it into a named misbehaviour on demand.

## Components

Each package is owned by one component (screaming architecture,
`bzh:screaming-architecture`) and states its own contract in an in-package `README.md`.

| Package                                                                        | Binary                                        | What it is                                                                                                                                                                    |
| ------------------------------------------------------------------------------ | --------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| [`forge`](src/blizzard_mock/forge/README.md)                                   | `blizzard-mock-forge`                         | Mock GitHub forge — the work-source and delivery seams over one vendor surface, backed by bare git repos.                                                                      |
| [`harness`](src/blizzard_mock/harness/README.md)                               | `mock-claude-code`, `mock-codex`, `mock-opencode` | Mock coding-harness engine — *the prompt is the program*: one shared `exec()` engine behind the per-harness CLI and wire facades the real adapters are tested against.     |
| [`fixture_workspace`](src/blizzard_mock/fixture_workspace/README.md)           | `blizzard-mock-fixture`                       | Fixture-workspace scaffold — mints bare `file://` origins (winter-free by default; a real winter workspace on request) for the runner-under-test to drive.                                                     |
| [`mock_hub`](src/blizzard_mock/mock_hub/README.md)                             | `blizzard-mock-hub`                           | The subset of the hub API a runner consumes, stateful, mirrored **without importing `blizzard`** — so the real runner can be built and service-tested against it.              |
| [`mock_runner`](src/blizzard_mock/mock_runner/README.md)                       | `blizzard-mock-runner`                        | The counterpart for the hub. The real runner is outbound-only, so this one is a *driver*, not a server: it performs register → peek → claim → complete, and its levers distort those calls. |
| [`idp`](src/blizzard_mock/idp/README.md)                                       | `blizzard-mock-idp`                           | Stub OAuth identity provider — generic OIDC and GitHub-style OAuth2 at one origin, with no login UI to drive.                                                                  |
| [`mock_data`](src/blizzard_mock/mock_data/README.md)                           | `blizzard-mock-data`                          | Mock-data CLI — creates and resets domain state in the hub and runner stores, and composes a whole ready-to-view board in one command (`scenario board`).                     |

Two primitives are shared rather than owned: `levers.py`, the explicit control that
steers a mock into a named edge state and may self-expire after a set number of affected
requests, and `clock.py`, the injected clock (`bzh:injected-clock`) that lets a test pin
an instant and assert exact timestamps.

Named, versioned fixture scenarios spanning stores (`blizzard-mock-data fixture`) remain
a stub.

## Toolchain

uv + ruff + pyright + pytest, per `blizzard-context:/standards/python.md`
(`bzh:python-toolchain`), plus a process-reference prose lint
(`blizzard-context:/standards/comments.md`, `bzh:comment-locality`). The gates a change
must pass — also wrapped as mise tasks `install` / `lint` / `format` / `typecheck` /
`test` / `process-ref-lint`, plus `gate` for all of them together:

```shell
uv sync
uv run ruff check .
uv run ruff format --check .
uv run pyright
uv run pytest
vale --output=line .
```

`uv run pytest` needs a sibling `blizzard` checkout: the wire-parity guard
(`tests/test_wire_parity.py`) compares the hub mirrors against that repo's committed
OpenAPI and fact-kind constants; `tests/test_real_runner_against_mock_hub.py` runs the
real runner against the mock hub. Both **fail** rather than skip when they cannot
resolve the checkout — parity never checked is not a green. A winter feature
environment supplies the sibling by construction; elsewhere, point `$BLIZZARD_SOURCE`
and `$BLIZZARD_RUNNER_BIN` at the checkout and its runner binary.

`vale --output=line .` (`styles/Blizzard/ProcessReference.yml` against `.vale.ini`'s
`[*.md]` and `[{src,tests}/**/*.py]` sections) fails on a repo or hub tracker number, an
issue or PR number, a review-finding id, a bare decision or finding id, a phase, or a
lettered-change token in a comment, docstring, or `.md` file — state the fact the token
stood for, or delete it. `mise run gate` (`./scripts/ci-gate.sh`) runs it alongside the
rest.

### CI

A PR gate (`.github/workflows/pr.yml`, on pull requests into `master` and `release/**`) and a push-to-master gate (`.github/workflows/push.yml`)
both run the reusable `gate.yml`: `quality` (`ruff format --check`, lint, typecheck),
`test` (`mise run test -- -m "not needs_blizzard"`), `process-ref-lint`, and a separate
`parity` job (`mise run test -- -m needs_blizzard`). Parity checks out blizzard at a
resolved commit: a same-named `v*` tag, the PR head's matching branch if it exists,
otherwise the target line (`master` or `release/X.Y`), and `master` on a master push.
The resolver logs the selected ref and commit. `.github/workflows/nightly.yml` calls the
same gate on a schedule with an explicit blizzard `master` ref. The check names are
`gate / ruff + pyright`, `gate / pytest (sans needs_blizzard)`,
`gate / process-reference lint`, and `gate / wire parity (needs_blizzard)`; parity is
optional until a green run supports requiring it in branch protection.

## Acceptance proof

`tests/test_acceptance_loop_e2e.py` (pytest marker `e2e`) is the fleet's standing
end-to-end proof: a scripted prompt, run through the mock harness in a fixture-workspace
environment, lands a commit that the mock forge merges to bare `main` — **with no
blizzard code involved at all**. It wires every component over its real seam: the forge
over HTTP, the harness over its façade CLI, git over real `file://` pushes and merges.

```shell
uv run pytest -m e2e
```

It skips unless `$BLIZZARD_MOCK_WINTER_SOURCE` names a local winter workspace.

## License

Source-available under the Functional Source License 1.1 (FSL-1.1-ALv2). Each release converts to
Apache License 2.0 two years after it ships. See [LICENSE](./LICENSE).
