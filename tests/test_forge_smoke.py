"""Smoke coverage for the mock GitHub forge. Grow into unit coverage (Build step)."""

from __future__ import annotations

from blizzard_mock.forge import cli


def test_forge_entrypoint_prints_usage(capsys) -> None:
    cli.main()
    out = capsys.readouterr().out
    assert "blizzard-mock-forge" in out
