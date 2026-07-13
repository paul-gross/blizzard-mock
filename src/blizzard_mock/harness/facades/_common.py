"""Shared plumbing every per-harness facade CLI reuses.

The facades differ only in flag names and wire format; reading the prompt from
an arg or stdin, translating a fence refusal into a CLI error, and dispatching
into the shared engine are identical across all three and live here.
"""

from __future__ import annotations

import sys

from blizzard_mock.harness.engine import FenceError, IHarnessWire, run_prompt

#: Exit code a facade returns when the engine's fence refuses the run. Distinct
#: from a behavior-script error (1) so callers can tell "refused" from "ran and
#: failed".
FENCE_EXIT_CODE = 2


def read_script(prompt_arg: str | None) -> str | None:
    """The behavior script: the positional ``prompt_arg``, else piped stdin, else ``None``.

    ``None`` signals "nothing to run" — the caller prints usage and exits 0,
    mirroring a bare ``claude`` invocation.
    """
    if prompt_arg is not None:
        return prompt_arg
    if not sys.stdin.isatty():
        try:
            piped = sys.stdin.read()
        except (OSError, ValueError):
            return None  # stdin closed / not readable (e.g. captured under pytest)
        if piped.strip():
            return piped
    return None


def dispatch(
    *,
    wire: IHarnessWire,
    script: str,
    session_id: str | None,
    is_resume: bool,
) -> int:
    """Run ``script`` through the engine, mapping a fence refusal to an error exit."""
    try:
        return run_prompt(script, wire=wire, session_id=session_id, is_resume=is_resume)
    except FenceError as exc:
        print(str(exc), file=sys.stderr)
        return FENCE_EXIT_CODE
