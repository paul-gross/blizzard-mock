"""The stub IdP's own identity vocabulary — the profile levers drive."""

from __future__ import annotations

from pydantic import BaseModel


class Profile(BaseModel):
    """The identity the next completed authorize dance resolves to.

    ``subject`` doubles as the GitHub-style numeric ``id`` (a caller sets a
    digit-string, e.g. ``"1001"``, to exercise the numeric-id conformer path) and the
    OIDC ``sub`` claim — the same stable subject either shape's conformer reads.

    ``role`` rides alongside the identity claims for a consuming hub to read and apply
    at first-login time; it is not itself an OIDC/GitHub claim, so it never surfaces in
    the signed ``id_token`` or the GitHub-shaped ``/user`` response — a hub without its
    own role-assignment API yet still applies the sqlite stand-in documented in this
    IdP's README.
    """

    subject: str = "1001"
    handle: str = "octocat"
    email: str | None = "octocat@example.com"
    email_verified: bool = True
    role: str | None = None


DEFAULT_PROFILE = Profile()
