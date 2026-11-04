"""Shared by OpenCode 2's standalone, stdlib-only fakes: the managed service, its registration and ``/api/*``
surface, the sessions and the operations a turn is built from, the permission rules, the corpus-shaped records, the
service-level levers, the takeover TUI, and the store migration. What a turn does is the :class:`TurnRunner` the
artifact shipping this package supplies; nothing here executes a prompt."""

from __future__ import annotations
