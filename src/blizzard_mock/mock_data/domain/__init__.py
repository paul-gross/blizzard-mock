"""The mock-data domain core (``bzh:domain-core``).

Depends on nothing outward — no SQLAlchemy, no click, no filesystem or network.
A concept composer (Phase 2+) turns a seedable concept into ``list[FactRow]``
(``facts.py``); the drift guard (``schema_contract.py``) validates a composed
row set against a schema-agnostic snapshot of the live store; ``seeding.py``
owns the ``ISeedStore`` seam a composition is handed to. ``internal/`` is the
only place that talks SQLAlchemy.
"""
