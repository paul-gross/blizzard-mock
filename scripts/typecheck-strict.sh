#!/usr/bin/env bash
# Report pyright strict errors over src and tests, with bare `# type: ignore` comments disabled.
# A report, not a gate: always exits 0. Pass pyright flags through, e.g. `--outputjson` for
# per-file and per-rule counts.
set -uo pipefail

cd "$(dirname "$0")/.."

config="$(mktemp --suffix=.json -p .)"
trap 'rm -f "$config"' EXIT

cat >"$config" <<'JSON'
{
  "include": ["src", "tests"],
  "pythonVersion": "3.12",
  "typeCheckingMode": "strict",
  "enableTypeIgnoreComments": false,
  "reportMissingTypeStubs": false
}
JSON

uv run pyright -p "$config" "$@"
exit 0
