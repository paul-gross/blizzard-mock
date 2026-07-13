"""Fixture-workspace scaffold.

Mints a generated, disposable, **real** winter workspace for the
runner-under-test to drive: a directory of bare git origin repos (addressed as
``file://`` remotes — no network, no real forge in the git path) and a real
winter workspace initialized against them, with a small committed history and a
``.winter/config.toml`` declaring them as project repos.

It lives under a per-env scratch path (keyed off the feature env, e.g.
``WINTER_ENV``) so two feature envs verifying at once never share a fixture, and
the bare origins it creates are the same ones the mock forge fronts (one git
truth).

See ``README.md`` for the full contract.
"""
