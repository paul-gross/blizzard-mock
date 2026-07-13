"""Outer-layer adapters for the fixture-workspace seams (git, winter CLI).

Everything that touches ``subprocess`` lives here, behind the Protocols the
domain declares in ``..service`` (``bzh:dependency-inversion``).
"""
