"""The stub IdP's own identity vocabulary — the profile levers drive."""

from __future__ import annotations

from pydantic import BaseModel, model_validator


class SecondaryEmail(BaseModel):
    """One more address on the account beside the primary, with its own verified flag."""

    email: str
    verified: bool = True


class Profile(BaseModel):
    """The identity the next completed authorize dance resolves to.

    ``subject`` doubles as the GitHub-style ``id`` and the OIDC ``sub`` claim. ``email`` is
    the account's primary address; ``secondary_emails`` are the account's other addresses,
    which only the GitHub-style ``/user/emails`` lists — an OIDC ``id_token`` carries one
    address.
    """

    subject: str = "1001"
    handle: str = "octocat"
    email: str | None = "octocat@example.com"
    email_verified: bool = True
    secondary_emails: tuple[SecondaryEmail, ...] = ()
    role: str | None = None

    @model_validator(mode="after")
    def _addresses_are_distinct(self) -> Profile:
        """An account lists each address once, the primary among them."""
        addresses = [entry.email.lower() for entry in self.secondary_emails]
        if self.email is not None:
            addresses.append(self.email.lower())
        if len(set(addresses)) != len(addresses):
            raise ValueError("an address appears twice among the primary and secondary emails")
        return self


DEFAULT_PROFILE = Profile()
