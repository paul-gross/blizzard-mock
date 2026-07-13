"""Smoke coverage for the mock-data CLI skeleton.

The CLI surface (verbs, help, contract) is real and asserted here; the verb
bodies raise until the domain models exist (bootstrap P5), which is also
asserted so the skeleton contract is pinned.
"""

from __future__ import annotations

from click.testing import CliRunner

from blizzard_mock.mock_data.cli import cli


def _runner() -> CliRunner:
    return CliRunner()


def test_root_help_describes_contract() -> None:
    result = _runner().invoke(cli, ["--help"])
    assert result.exit_code == 0
    # The three commands are on the surface.
    for verb in ("reset", "create", "fixture"):
        assert verb in result.output


def test_verbs_expose_help() -> None:
    runner = _runner()
    for args in (
        ["reset", "--help"],
        ["create", "--help"],
        ["fixture", "--help"],
        ["fixture", "list", "--help"],
        ["fixture", "apply", "--help"],
    ):
        result = runner.invoke(cli, args)
        assert result.exit_code == 0, args


def test_reset_is_skeleton() -> None:
    result = _runner().invoke(cli, ["reset", "--store", "hub"])
    assert result.exit_code == 1
    assert "not implemented yet" in result.output


def test_create_is_skeleton() -> None:
    result = _runner().invoke(cli, ["create", "chunk", "--store", "runner"])
    assert result.exit_code == 1
    assert "not implemented yet" in result.output


def test_fixture_apply_is_skeleton() -> None:
    result = _runner().invoke(cli, ["fixture", "apply", "parked-on-question"])
    assert result.exit_code == 1
    assert "not implemented yet" in result.output
