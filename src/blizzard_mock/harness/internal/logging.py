"""structlog wiring for the mock harness, routed to **stderr**.

The harness's *stdout* is a wire surface the runner's adapter parses (Claude
Code JSON, Codex JSONL, …); diagnostics must never corrupt it. So every log line
goes to stderr, with the renderer chosen by TTY per ``bzh:structlog-logging`` —
JSON when piped (CI / an agent reads it), a colored console when interactive.
"""

from __future__ import annotations

import sys

import structlog

_configured = False


def get_logger(name: str) -> structlog.stdlib.BoundLogger:
    """Return a structlog logger that writes to stderr, configuring once."""
    global _configured
    if not _configured:
        renderer: structlog.types.Processor = (
            structlog.dev.ConsoleRenderer() if sys.stderr.isatty() else structlog.processors.JSONRenderer()
        )
        structlog.configure(
            processors=[
                structlog.processors.add_log_level,
                structlog.processors.TimeStamper(fmt="iso"),
                renderer,
            ],
            logger_factory=structlog.PrintLoggerFactory(file=sys.stderr),
            cache_logger_on_first_use=True,
        )
        _configured = True
    return structlog.get_logger(name)
