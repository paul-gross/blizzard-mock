"""Dependency-free domain core of the mock GitHub forge.

Holds the forge's business rules and the seams it declares (git backend, state
store, levers, clock) — no FastAPI, no GitPython, no click (``bzh:domain-core``).
The outer layers (``forge.internal`` adapters, ``forge.api`` routers) depend
inward on these types; nothing here imports outward.
"""
