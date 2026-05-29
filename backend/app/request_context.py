# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Helpers that pull request-scoped context (IP, user-agent) for audit rows."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from fastapi import Request


def extract_request_context(
    request: Request | None,
) -> tuple[str | None, str | None]:
    """Return (ip_address, user_agent) from a Request, or (None, None).

    The client IP is read from the proxy-appended (right) end of
    X-Forwarded-For, not the client-spoofable leftmost entry — otherwise a
    caller could forge the source IP recorded in the audit trail. See
    ``Settings.trusted_proxy_hops``.
    """
    if request is None:
        return None, None

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
