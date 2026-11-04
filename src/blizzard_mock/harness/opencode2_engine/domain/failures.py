"""The errors a turn ends with when its provider refuses it, shaped as 2.1.4's ``Session.StructuredError``
(``{type, message, status?, response?.body?, retryAfterMs?}``) and typed as the committed corpus records them."""

from __future__ import annotations

import json
from collections.abc import Mapping

type Json = dict[str, object]

#: Every ``type`` a usage limit is reported under, by what imposed it.
USAGE_LIMIT_TYPES = ("provider.rate-limit", "provider.quota", "provider.free-limit")


def provider_refusal(message: str = "The provider refused the request: content filtered") -> Json:
    return {"type": "provider.content-filter", "message": message}


def usage_limit(retry_after_ms: int | None = 18_748_000, *, kind: str = "provider.free-limit") -> Json:
    """A usage limit; ``retry_after_ms`` is the provider's reset, omitted when it sends none."""
    if kind not in USAGE_LIMIT_TYPES:
        raise ValueError(f"not a usage-limit type: {kind}")
    message = "Free usage limit reached. Connect a provider or subscribe to keep going, or switch models."
    body = {"type": "error", "error": {"type": "FreeUsageLimitError", "message": message}}
    error: Json = {"type": kind, "message": message, "status": 429, "response": {"body": json.dumps(body)}}
    if retry_after_ms is not None:
        error["retryAfterMs"] = retry_after_ms
    return error


def overload(message: str = "The provider is overloaded") -> Json:
    return {"type": "provider.internal", "message": message, "status": 529}


def unknown_model(model: Mapping[str, object]) -> Json:
    return {"type": "provider.no-route", "message": f"Model unavailable: {model.get('providerID')}/{model.get('id')}"}


def unknown_variant(model: Mapping[str, object]) -> Json:
    reference = f"{model.get('providerID')}/{model.get('id')}"
    return {"type": "provider.no-route", "message": f"Variant unavailable for {reference}: {model.get('variant')}"}


def script_failed(message: str) -> Json:
    """A script that raised, or exited without finishing its turn."""
    return {"type": "unknown", "message": message}


def unfenced(message: str) -> Json:
    return {"type": "unknown", "message": message}


__all__ = [
    "USAGE_LIMIT_TYPES",
    "overload",
    "provider_refusal",
    "script_failed",
    "unfenced",
    "unknown_model",
    "unknown_variant",
    "usage_limit",
]
