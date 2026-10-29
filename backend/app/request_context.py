# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Helpers that pull request-scoped context (IP, user-agent) for audit rows."""

from __future__ import annotations

import hmac
import ipaddress
import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from fastapi import Request

logger = logging.getLogger(__name__)

# Headers the web app's server sets when it calls the backend while rendering a
# page, naming the end user it is rendering for. Honoured only alongside a
# matching FORWARD_AUTH_HEADER (``Settings.frontend_forward_secret``): anyone
# can send a header, so without the secret they are ignored.
FORWARDED_CLIENT_IP_HEADER = "X-Pablo-Client-IP"
FORWARDED_CLIENT_UA_HEADER = "X-Pablo-Client-UA"
FORWARD_AUTH_HEADER = "X-Pablo-Frontend-Auth"

# Longer than any real browser's agent string; an audit column is not a place
# to store whatever a caller cares to send.
MAX_USER_AGENT_LENGTH = 512


def _accepted_forward_secrets() -> list[bytes]:
    from .settings import get_settings  # noqa: PLC0415

    raw = get_settings().frontend_forward_secret.get_secret_value()
    return [s.strip().encode() for s in raw.split(",") if s.strip()]


def _forward_secret_matches(presented: str, secrets: list[bytes]) -> bool:
    candidate = presented.encode()
    # Compare against every slot, so the time taken does not say which matched.
    matched = False
    for secret in secrets:
        matched |= hmac.compare_digest(candidate, secret)
    return matched


def _forwarded_client(request: Request) -> tuple[str, str | None] | None:
    """The end user's (ip, user_agent) as the web app's server forwarded them.

    None unless forwarding is configured, the request proves it comes from the
    web app's server, and the forwarded IP is a valid address.
    """
    presented = request.headers.get(FORWARD_AUTH_HEADER)
    if presented is None:
        return None
    secrets = _accepted_forward_secrets()
    if not secrets:
        return None
    if not _forward_secret_matches(presented, secrets):
        logger.warning("forwarded_client_rejected reason=bad_secret")
        return None

    raw_ip = (request.headers.get(FORWARDED_CLIENT_IP_HEADER) or "").strip()
    try:
        ip = str(ipaddress.ip_address(raw_ip))
    except ValueError:
        logger.warning("forwarded_client_rejected reason=invalid_ip")
        return None

    user_agent = request.headers.get(FORWARDED_CLIENT_UA_HEADER)
    if user_agent is not None:
        user_agent = user_agent[:MAX_USER_AGENT_LENGTH] or None
    return ip, user_agent


def extract_request_context(
    request: Request | None,
) -> tuple[str | None, str | None]:
    """Return (ip_address, user_agent) from a Request, or (None, None).

    The client IP is read from the proxy-appended (right) end of
    X-Forwarded-For, not the client-spoofable leftmost entry — otherwise a
    caller could forge the source IP recorded in the audit trail. See
    ``Settings.trusted_proxy_hops``.

    A request the web app's server makes while rendering a page would record
    the server's address and agent, not the user's. When
    ``Settings.frontend_forward_secret`` is set and the request carries it, the
    user's IP and agent the server forwarded are used instead.
    """
    if request is None:
        return None, None

    forwarded_client = _forwarded_client(request)
    if forwarded_client is not None:
        return forwarded_client

    from .settings import get_settings  # noqa: PLC0415

    forwarded = request.headers.get("X-Forwarded-For")
    parts = [p.strip() for p in forwarded.split(",") if p.strip()] if forwarded else []
    if parts:
        hops = min(get_settings().trusted_proxy_hops, len(parts))
        ip: str | None = parts[-hops]
    elif request.client:
        ip = request.client.host
    else:
        ip = None
    return ip, request.headers.get("User-Agent")
