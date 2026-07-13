"""Mock GitHub forge.

A standalone mock of the GitHub API subset blizzard touches, covering two seams
with one vendor surface: the work-source seam (issues with bodies and comment
threads, served vendor-native) and the delivery seam (PRs, merges, and the
states the merge queue must survive).

Its backing model is a directory of bare git repos — the same ``file://``
origins the fixture-workspace's worktrees push to. Issue and PR metadata are
mock state, but mergeability is computed against real refs and merging performs
a real merge into the bare repo's ``main``.

Stateful and levered: externally-merged PR, merge conflict, merge rejected,
comment added mid-flight, rate-limited, token rejected, unreachable.

See ``README.md`` for the full contract.
"""
