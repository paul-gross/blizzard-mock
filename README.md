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
| [`fixture_workspace`](src/blizzard_mock/fixture_workspace/README.md)           | `blizzard-mock-fixture`                       | Fixture-workspace scaffold — mints bare `file://` origins plus a real winter workspace for the runner-under-test to drive.                                                     |
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
(`bzh:python-toolchain`). The gates a change must pass — also wrapped as mise tasks
`install` / `lint` / `format` / `typecheck` / `test`:

```shell
uv sync
uv run ruff check .
uv run ruff format --check .
uv run pyright
uv run pytest
```

`uv run pytest` needs a sibling `blizzard` checkout: the wire-parity guard
(`tests/test_wire_parity.py`) compares the hub mirrors against that repo's committed
OpenAPI and fact-kind constants, and **fails** rather than skips when it cannot resolve
one — parity it never checked is not a green. A winter feature environment supplies the
sibling by construction; elsewhere, point `$BLIZZARD_SOURCE` at the checkout.

## Acceptance proof

`tests/test_acceptance_loop_e2e.py` (pytest marker `e2e`) is the fleet's standing
end-to-end proof: a scripted prompt, run through the mock harness in a fixture-workspace
environment, lands a commit that the mock forge merges to bare `main` — **with no
blizzard code involved at all**. It wires every component over its real seam: the forge
over HTTP, the harness over its façade CLI, git over real `file://` pushes and merges.

```shell
uv run pytest -m e2e
```

It skips when no local winter source is discoverable (an enclosing winter workspace, or
`$BLIZZARD_MOCK_WINTER_SOURCE`).
