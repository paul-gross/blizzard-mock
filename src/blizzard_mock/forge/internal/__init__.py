"""Outer-layer adapters implementing the forge's domain seams.

Everything under ``internal/`` is package-private (``bzh:dependency-inversion``):
the GitPython git backend, the in-memory state and lever stores, and the git
error factory. Nothing outside ``forge`` imports these directly.
"""
