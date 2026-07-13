"""Mock-data CLI.

A tool *for agents* to set up hub/runner test cases repeatably. It operates on
**domain models**, not raw tables — an agent asks for "a chunk parked on a
question", not for rows. Larger data sets are instantiated from **fixtures** —
named, versioned scenarios the suite and agents share. **Reset** returns a store
to a known-clean state, so every run starts from the same ground.

A named fixture mints a *consistent world* across three state holders that must
agree — hub/runner store rows, mock-forge state, and fixture-workspace git state
(``implementation/verification.md``, "Scenario consistency").

This package is a **skeleton**: the CLI surface (verbs, help, contract) is real,
but the verbs raise until the domain models they operate on exist. See
``README.md``.
"""
