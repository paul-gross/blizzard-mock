"""Adapter guts for the mock harness — real git plumbing and stderr logging.

Kept out of the framework-free core (``engine.py``, ``session.py``) and out of
the script-facing helper surface (``helpers.py``); these modules do real I/O.
"""
