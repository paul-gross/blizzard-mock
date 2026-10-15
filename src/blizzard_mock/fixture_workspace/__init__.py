"""Fixture-workspace scaffold.

Mints a generated, disposable fixture workspace for the runner-under-test to
drive: bare git origin repos addressed as ``file://`` remotes. Winter-free by
default; given a local winter source it also initializes a real winter workspace
against them. See ``README.md`` for the contract.
"""
