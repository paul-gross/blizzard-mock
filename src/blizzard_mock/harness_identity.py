"""The one coding-harness owner every pre-provenance mock row belongs to.

Shared the same way :mod:`blizzard_mock.clock` is: a single top-level primitive
``mock_data``'s runner-store composers and ``mock_runner``'s driven wire payloads both
need, neither importing the other to get it.
"""

from __future__ import annotations

#: The only historical harness every pre-provenance session backfills to; both mocks' compatibility default.
CLAUDE_CODE_HARNESS_ID = "claude_code"
