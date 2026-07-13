"""Repo-wide smoke: the four domain packages import cleanly."""

from __future__ import annotations

import importlib


def test_domain_packages_import() -> None:
    for name in (
        "blizzard_mock",
        "blizzard_mock.forge",
        "blizzard_mock.fixture_workspace",
        "blizzard_mock.harness",
        "blizzard_mock.mock_data",
    ):
        assert importlib.import_module(name) is not None
