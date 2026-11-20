# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""A client for Anthropic's own API, for deployments with their own key.

The structured gateway reaches Claude through a cloud provider by default
(``anthropic:`` on Vertex AI, ``bedrock:`` on Amazon Bedrock). A deployment
that holds its own Anthropic key can instead name ``anthropic-direct:<model>``,
which calls the API directly through the same gateway, so the call keeps the
gateway's request shape and its usage record.

Two settings, both required:

- ``anthropic_direct_api_key``, kept apart from ``ANTHROPIC_API_KEY`` on
  purpose: SDKs and command-line tools read that variable on their own, and
  this key should reach only the calls that ask for this route.
- ``anthropic_direct_features``, the AI features allowed to use it (``*`` for
  any). The feature is the one the call runs under (``llm_feature``), so a
  model setting elsewhere that happens to name the prefix cannot send another
  feature's data on the key.

The key is read when a client is built and handed to the SDK, nothing else: no
log line or error raised here carries it.
"""

from __future__ import annotations

from typing import Any

from .llm_telemetry import llm_feature_var
from .vertex_client import DEFAULT_VERTEX_TIMEOUT_SECONDS

# The same retry policy as the Vertex client: retries live in the caller's
# ``call_with_retry``, not in the SDK.
_SDK_MAX_RETRIES = 0

_ANY_FEATURE = "*"


class AnthropicDirectNotConfiguredError(RuntimeError):
    """An ``anthropic-direct:`` call with no key, or from a feature not allowed to use it."""


def _route_key() -> str:
    from ..settings import get_settings

    return get_settings().anthropic_direct_api_key.get_secret_value().strip()


def _allowed_features() -> frozenset[str]:
    from ..settings import get_settings

    raw = get_settings().anthropic_direct_features
    return frozenset(f.strip() for f in raw.split(",") if f.strip())


def anthropic_direct_allows(feature: str | None) -> bool:
    """Whether ``feature`` may call ``anthropic-direct:`` models."""
    allowed = _allowed_features()
    return _ANY_FEATURE in allowed or (feature is not None and feature in allowed)


def anthropic_direct_configured(feature: str | None) -> bool:
    """Whether an ``anthropic-direct:`` call from ``feature`` would be sent.

    Lets a caller skip the route before building a request, rather than
    finding out from the error such a call raises.
    """
    return bool(_route_key()) and anthropic_direct_allows(feature)


def check_anthropic_direct_call() -> None:
    """Raise unless the feature now running may use the route and a key is set.

    Raises:
        AnthropicDirectNotConfiguredError: no key, or the feature is not allowed.
    """
    feature = llm_feature_var.get()
    if not anthropic_direct_allows(feature):
        msg = f"feature {feature!r} is not allowed to call anthropic-direct models"
        raise AnthropicDirectNotConfiguredError(msg)
    if not _route_key():
        msg = "the anthropic-direct route has no key configured"
        raise AnthropicDirectNotConfiguredError(msg)


def anthropic_direct_client(
    *,
    timeout_seconds: float = DEFAULT_VERTEX_TIMEOUT_SECONDS,
    http_client: Any = None,
) -> Any:
    """Return a new client for Anthropic's API, built with the route's key.

    ``http_client`` is an ``httpx.Client`` for tests that fake the API at the
    transport.

    Raises:
        AnthropicDirectNotConfiguredError: no key is configured.
        RuntimeError: the anthropic package is not installed.
    """
    key = _route_key()
    if not key:
        msg = "the anthropic-direct route has no key configured"
        raise AnthropicDirectNotConfiguredError(msg)
    try:
        from anthropic import Anthropic
    except ImportError as exc:
        raise RuntimeError("anthropic package is required for Anthropic API access") from exc
    extra: dict[str, Any] = {"http_client": http_client} if http_client is not None else {}
    return Anthropic(
        api_key=key,
        timeout=timeout_seconds,
        max_retries=_SDK_MAX_RETRIES,
        **extra,
    )


__all__ = [
    "AnthropicDirectNotConfiguredError",
    "anthropic_direct_allows",
    "anthropic_direct_client",
    "anthropic_direct_configured",
    "check_anthropic_direct_call",
]
