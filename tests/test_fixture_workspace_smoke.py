"""Smoke coverage for the fixture-workspace scaffold. Grow into unit coverage (Build step)."""

from __future__ import annotations

from blizzard_mock.fixture_workspace import cli


def test_fixture_entrypoint_prints_usage(capsys) -> None:
    cli.main()
    out = capsys.readouterr().out
    assert "blizzard-mock-fixture" in out
