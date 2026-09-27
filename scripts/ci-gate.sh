#!/usr/bin/env bash
# The local equivalent of the PR-to-master merge gate.
#
# Runs exactly the checks the `pr` GitHub Actions workflow runs, in one command:
#   ruff format --check · ruff check · pyright · pytest (sans needs_blizzard) ·
#   process-reference prose lint
#
# Invoke as `mise run gate` or `./scripts/ci-gate.sh`.
set -euo pipefail

cd "$(dirname "$0")/.."

step() { printf '\n\033[1;36m==> %s\033[0m\n' "$*"; }

step "ruff format --check ."
uv run ruff format --check .

step "ruff check . (mise run lint)"
mise run lint

step "pyright (mise run typecheck)"
mise run typecheck

step "pytest -m \"not needs_blizzard\" (mise run test)"
mise run test -- -m "not needs_blizzard"

step "process-reference prose lint: vale --output=line ."
vale --output=line .

step "Gate passed."
