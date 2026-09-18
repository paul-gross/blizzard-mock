"""Pure decision logic for the fake OpenCode CLI surface.

No file I/O, no sockets, no argparse, no subprocess — everything here is a
plain function from ``(lever set, request shape, session state)`` to
``(JSON body / exit code / text)``. ``cli.py`` and ``internal/`` do the I/O
and call into these.
"""

from __future__ import annotations
