"""The stub IdP's own identity vocabulary — the profile levers drive."""

from __future__ import annotations

from pydantic import BaseModel


class Profile(BaseModel):
    """The identity the next completed authorize dance resolves to.

    ``subject`` doubles as the GitHub-style ``id`` and the OIDC ``sub`` claim.
    """

    subject: str = "1001"
    handle: str = "octocat"
    email: str | None = "octocat@example.com"
    email_verified: bool = True
    role: str | None = None


DEFAULT_PROFILE = Profile()
