"""The mock-data domain core (``bzh:domain-core``).

Depends on nothing outward — no SQLAlchemy, no click, no filesystem or
network. A concept composer turns a seedable concept into
``list[FactRow]``; ``internal/`` is the only place that talks SQLAlchemy.
"""
