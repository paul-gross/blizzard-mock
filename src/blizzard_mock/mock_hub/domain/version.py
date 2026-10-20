"""The version the mock hub answers as its own, and the compatibility window it admits under."""

from __future__ import annotations

from blizzard_mock.mock_hub.domain.wire import VersionWindowView

#: The mock hub's own version — the real hub's at its line.
HUB_VERSION = "0.2.0"
#: The window answered on every admitted registration: the floor and ceiling of ``HUB_VERSION``'s line.
WINDOW = VersionWindowView(floor="0.2", ceiling="0.2")
