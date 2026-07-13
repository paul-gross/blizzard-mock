"""Errors the fixture-workspace domain raises.

Factory-injected into the adapters (``bzh:dependency-inversion``) so the outer
subprocess layer signals failure in the domain's vocabulary rather than leaking
``subprocess.CalledProcessError`` outward.
"""

from __future__ import annotations


class FixtureError(Exception):
    """A fixture could not be minted, torn down, or located as requested."""
