# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""A client for Anthropic's own API, for deployments with their own key.

The structured gateway reaches Claude through a cloud provider by default
(``anthropic:`` on Vertex AI, ``bedrock:`` on Amazon Bedrock). A deployment
that holds its own Anthropic key can instead name ``anthropic-api:<model>``,
which calls the API directly through the same gateway, so the call keeps the
gateway's request shape and its usage record.

The key is the ``anthropic_api_route_key`` setting, kept apart from
``ANTHROPIC_API_KEY`` on purpose: SDKs and command-line tools read that
variable on their own, and this key should reach only the calls that ask for
this route. It is read when a client is built, handed to the SDK and nothing
else: no log line or error raised here carries it.
"""

from __future__ import annotations

from typing import Any

from .vertex_client import DEFAULT_VERTEX_TIMEOUT_SECONDS

# The same retry policy as the Vertex client: retries live in the caller's
# ``call_with_retry``, not in the SDK.
_SDK_MAX_RETRIES = 0


class AnthropicApiRouteNotConfiguredError(RuntimeError):
    """An ``anthropic-api:`` call was made with no key configured."""

    def __init__(self) -> None:
        super().__init__(
            "the anthropic-api route has no key configured (setting anthropic_api_route_key)"
        )


def _route_key() -> str:
    from ..settings import get_settings

    return get_settings().anthropic_api_route_key.get_secret_value().strip()


def anthropic_api_route_configured() -> bool:
    """Whether ``anthropic-api:`` calls have a key to use.

    Lets a caller skip the route before building a request, rather than
    finding out from the error a call without a key raises.
    """
    return bool(_route_key())


def anthropic_api_client(
    *,
    timeout_seconds: float = DEFAULT_VERTEX_TIMEOUT_SECONDS,
    http_client: Any = None,
) -> Any:
    """Return a new client for Anthropic's API, built with the route's key.

    ``http_client`` is an ``httpx.Client`` for tests that fake the API at the
    transport.

    Raises:
        AnthropicApiRouteNotConfiguredError: no key is configured.
        RuntimeError: the anthropic package is not installed.
    """
    key = _route_key()
    if not key:
        raise AnthropicApiRouteNotConfiguredError
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
    "AnthropicApiRouteNotConfiguredError",
    "anthropic_api_client",
    "anthropic_api_route_configured",
]
