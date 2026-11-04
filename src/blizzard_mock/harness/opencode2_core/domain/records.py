"""The fixed, corpus-shaped records the service answers with: ids, the model listing, usage, and errors."""

from __future__ import annotations

import secrets
import threading
import time
from collections.abc import Mapping

_COUNTER = [0]
_COUNTER_LOCK = threading.Lock()

DEFAULT_MODEL: dict[str, object] = {"providerID": "opencode", "id": "pulse-alpha-free"}
_PRICED = [{"input": 1.25, "output": 10.0, "cache": {"read": 0.125, "write": 0}}]
_FREE = [{"input": 0, "output": 0, "cache": {"read": 0, "write": 0}}]
_MODELS: tuple[tuple[str, str, str, tuple[str, ...], bool], ...] = (
    ("opencode", "pulse-alpha-free", "Pulse Alpha Free", ("low", "high", "max"), False),
    ("openai", "gpt-6-luna", "GPT-6 Luna", ("none", "low", "medium", "high", "xhigh"), True),
    ("openai", "gpt-6.1-terra", "GPT-6.1 Terra", ("low", "medium", "high"), True),
)


def now_ms() -> int:
    return int(time.time() * 1000)


def default_title() -> str:
    """The title a session created without one gets, as 2.1.4 names it."""
    return "New session - " + time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime()) + ".000Z"


def new_id(prefix: str) -> str:
    """Sortable within and across service lifetimes: the time, a counter, then randomness."""
    with _COUNTER_LOCK:
        _COUNTER[0] = (_COUNTER[0] + 1) % 0x10000
        count = _COUNTER[0]
    return f"{prefix}_{time.time_ns() // 1000:014x}{count:04x}{secrets.token_hex(4)}"


def model_listing(*, priced: bool) -> list[dict[str, object]]:
    listing: list[dict[str, object]] = []
    for provider, model_id, name, variants, paid in _MODELS:
        cost: list[object] = (list(_PRICED) if paid else list(_FREE)) if priced else []
        listing.append(
            {
                "id": model_id,
                "modelID": model_id,
                "providerID": provider,
                "name": name,
                "enabled": True,
                "status": "active",
                "capabilities": {"input": ["text"], "output": ["text"], "temperature": False, "tools": True},
                "limit": {"context": 400000, "output": 128000},
                "cost": cost,
                "variants": [{"id": variant} for variant in variants],
            }
        )
    return listing


def route_error(model: Mapping[str, object]) -> dict[str, object] | None:
    """The ``provider.no-route`` error a turn on ``model`` fails with, or ``None`` when it routes."""
    provider, model_id, variant = model.get("providerID"), model.get("id"), model.get("variant")
    entry = next((m for m in _MODELS if m[0] == provider and m[1] == model_id), None)
    if entry is None:
        return {"type": "provider.no-route", "message": f"Model unavailable: {provider}/{model_id}"}
    if variant is not None and variant not in entry[3]:
        return {"type": "provider.no-route", "message": f"Variant unavailable for {provider}/{model_id}: {variant}"}
    return None


def usage(model: Mapping[str, object], prompt: str, reply: str) -> tuple[dict[str, object], float]:
    """One step's tokens and its cost under the listing's price for ``model``."""
    tokens_in, tokens_out = 4700 + len(prompt) // 4, 10 + len(reply) // 4
    paid = any(m[0] == model.get("providerID") and m[1] == model.get("id") and m[4] for m in _MODELS)
    cost = (tokens_in * 1.25 + tokens_out * 10.0) / 1_000_000 if paid else 0
    return {"input": tokens_in, "output": tokens_out, "reasoning": 0, "cache": {"read": 0, "write": 0}}, cost


def service_info(version: str, url: str, pid: int) -> dict[str, object]:
    return {
        "version": version,
        "pid": pid,
        "urls": [url],
        "paths": {},
        "capabilities": {"sessions": True, "events": True},
    }


__all__ = [
    "DEFAULT_MODEL",
    "default_title",
    "model_listing",
    "new_id",
    "now_ms",
    "route_error",
    "service_info",
    "usage",
]
