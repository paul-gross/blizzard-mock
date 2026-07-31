"""Outer-layer adapters for the mock-data seams (SQLAlchemy reflection, runtime-dir config).

Everything that imports ``sqlalchemy`` or reads a ``blizzard-hub.toml``/
``blizzard-runner.toml`` off disk lives here, behind the Protocol the domain
declares in ``../domain/seeding.py`` (``bzh:dependency-inversion``).
"""
