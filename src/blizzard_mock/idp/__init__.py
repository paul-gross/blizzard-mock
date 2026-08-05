"""``blizzard_mock.idp`` — the stub OAuth identity provider (issue #92).

Serves both shapes at one origin: a generic OIDC provider and a GitHub-style
OAuth2 provider. Skips any login UI — ``authorize`` redirects back with a
code for whichever profile ``/_levers/profile`` currently holds.
"""

from __future__ import annotations
