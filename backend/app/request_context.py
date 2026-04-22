# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Helpers that pull request-scoped context (IP, user-agent) for audit rows."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from fastapi import Request


def extract_request_context(
    request: Request | None,
) -> tuple[str | None, str | None]:
    """Return (ip_address, user_agent) from a Request, or (None, None)."""
    if request is None:
        return None, None
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        ip = forwarded.split(",")[0].strip()
    elif request.client:
        ip = request.client.host
    else:
        ip = None
    return ip, request.headers.get("User-Agent")
