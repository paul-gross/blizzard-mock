"""structlog wiring for the mock harness, routed to **stderr**.

Stdout is a wire surface (Claude Code JSON, Codex JSONL, …); diagnostics must
never corrupt it. Renderer chosen by TTY per ``bzh:structlog-logging`` — JSON
when piped, a colored console when interactive.
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
