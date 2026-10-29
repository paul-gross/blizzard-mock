#!/usr/bin/env bash
# Resolve a blizzard commit for the mock's parity gate. The remote is injectable
# so the selection can be tested against a local bare repository.
set -euo pipefail

url="${BLIZZARD_URL:?BLIZZARD_URL is required}"
event="${EVENT_NAME:-}"
ref="${REF:-}"
base="${BASE_REF:-}"
head="${HEAD_REF:-}"
explicit="${EXPLICIT_REF:-}"

fail() {
  echo "::error::resolve-blizzard-ref: $*" >&2
  exit 1
}

branch_sha() {
  git ls-remote --heads "$url" "refs/heads/$1" | awk -v want="refs/heads/$1" '$2 == want { print $1 }'
}

tag_sha() {
  local out
  out="$(git ls-remote --tags "$url" "refs/tags/$1" "refs/tags/$1^{}")"
  awk -v peeled="refs/tags/$1^{}" '$2 == peeled { print $1 }' <<< "$out" | grep . ||
    awk -v want="refs/tags/$1" '$2 == want { print $1 }' <<< "$out"
}

emit() {
  echo "ref=$1"
  echo "sha=$2"
}

if [ -n "$explicit" ]; then
  sha="$(branch_sha "$explicit")"
  if [ -z "$sha" ]; then
    sha="$(tag_sha "$explicit" || true)"
  fi
  [ -n "$sha" ] || fail "blizzard has no branch or tag '$explicit'"
  emit "$explicit" "$sha"
  exit 0
fi

case "$ref" in
  refs/tags/v*)
    tag="${ref#refs/tags/}"
    sha="$(tag_sha "$tag" || true)"
    [ -n "$sha" ] || fail "blizzard has no tag '$tag'"
    emit "$tag" "$sha"
    exit 0
    ;;
esac

case "$event" in
  pull_request)
    case "$base" in
      master | release/*) ;;
      *) fail "a pull request into '$base' matches no blizzard-ref rule" ;;
    esac
    if [ -n "$head" ]; then
      sha="$(branch_sha "$head")"
      if [ -n "$sha" ]; then
        emit "$head" "$sha"
        exit 0
      fi
    fi
    selected="$base"
    ;;
  push)
    [ "$ref" = refs/heads/master ] || fail "a push to '$ref' matches no blizzard-ref rule"
    selected=master
    ;;
  *) fail "event '$event' on '$ref' matches no blizzard-ref rule" ;;
esac

sha="$(branch_sha "$selected")"
[ -n "$sha" ] || fail "blizzard has no branch '$selected'"
emit "$selected" "$sha"
