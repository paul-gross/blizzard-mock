"""``blizzard_mock.idp`` — the stub OAuth identity provider (issue #92).

The service/e2e-tier counterpart to blizzard's ``hub/auth/oauth/`` provider seam: a
real HTTP server serving **both** shapes the seam supports at one origin — a generic
**OIDC** provider (discovery + signed ``id_token``, RS256) and a **GitHub-style**
OAuth2 provider (``GET /user``, ``GET /user/emails``). Unlike a real provider it skips
any login UI: ``authorize`` immediately redirects back with a code for whichever
:class:`~blizzard_mock.idp.domain.models.Profile` the ``/_levers/profile`` control
currently holds — the levered edge-state control every mock in this repo exposes
(``implementation/mocking.md``).
"""

from __future__ import annotations
