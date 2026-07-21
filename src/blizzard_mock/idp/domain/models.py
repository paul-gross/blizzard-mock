"""The stub IdP's own identity vocabulary — the profile levers drive."""

from __future__ import annotations

from pydantic import BaseModel


class Profile(BaseModel):
    """The identity the next completed authorize dance resolves to.

    ``subject`` doubles as the GitHub-style numeric ``id`` (a caller sets a
    digit-string, e.g. ``"1001"``, to exercise the numeric-id conformer path) and the
    OIDC ``sub`` claim — the same stable subject either shape's conformer reads.
    """

    subject: str = "1001"
    handle: str = "octocat"
    email: str | None = "octocat@example.com"
    email_verified: bool = True


DEFAULT_PROFILE = Profile()
