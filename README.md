# blizzard-mock

The **mock fleet** for blizzard. Everything blizzard integrates with is a pluggable
seam (`blizzard-context:/architecture/system-shape.md`, `bzh:pluggable-seams`), and
every seam gets a controllable mock here. The fleet exists for one reason: agents
building blizzard must be able to construct any state — including rare edge cases —
deterministically, cheaply, and **with no real tokens and no network**.

Design source of truth: the discovery corpus `implementation/mocking.md` and
`implementation/verification.md`.

## Components

Four domain packages, each owned by one component (screaming architecture,
`bzh:screaming-architecture`). Each has an in-package `README.md` stating its
contract.

| Package | Binary | What it is |
|---------|--------|------------|
| [`blizzard_mock.forge`](src/blizzard_mock/forge/README.md) | `blizzard-mock-forge` | Mock GitHub forge — the work-source + delivery seams over one vendor surface, backed by bare git repos. |
| [`blizzard_mock.fixture_workspace`](src/blizzard_mock/fixture_workspace/README.md) | `blizzard-mock-fixture` | Fixture-workspace scaffold — mints bare `file://` origins + a real winter workspace for the runner-under-test to drive. |
| [`blizzard_mock.harness`](src/blizzard_mock/harness/README.md) | `mock-claude-code` (+ future codex / opencode facades) | Mock coding-harness engine — *the prompt is the program*: the shared `exec()` engine plus the per-harness CLI/wire facades the adapters are tested against. |
| [`blizzard_mock.mock_data`](src/blizzard_mock/mock_data/README.md) | `blizzard-mock-data` | Mock-data CLI — creates / resets domain-model state and instantiates named, versioned fixture scenarios in the hub and runner stores. |

## Toolchain

uv + ruff + pyright + pytest, per `blizzard-context:/standards/python.md`
(`bzh:python-toolchain`). Gates a change must pass:

```
uv sync
uv run ruff check .
uv run ruff format --check .
uv run pyright
uv run pytest
```

(mise tasks `install` / `lint` / `format` / `typecheck` / `test` wrap these.)

## Acceptance proof

`tests/test_acceptance_loop_e2e.py` (pytest marker `e2e`) is the fleet's standing
end-to-end proof — the P4 exit criterion of `implementation/bootstrap.md`: a
scripted prompt, run through the mock harness in a fixture-workspace env, lands a
commit the mock forge merges to bare `main`, with no blizzard code involved. It
wires every component over its real seam (forge over HTTP, harness over its façade
CLI, git over real `file://` pushes and merges). Run it with:

```
uv run pytest -m e2e
```

It is skipped when no local winter source is discoverable (an enclosing winter
workspace, or `$BLIZZARD_MOCK_WINTER_SOURCE`).
