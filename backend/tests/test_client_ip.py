# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Client-IP extraction must read the trusted (proxy-appended) end of
X-Forwarded-For, never the client-spoofable leftmost entry.

Regression for the rate-limiter bypass: a caller sending a unique forged
leftmost XFF per request would otherwise get a fresh limiter key every
time, and could forge the source IP recorded in the audit trail.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from app.rate_limit import get_client_ip
from app.request_context import extract_request_context
from starlette.requests import Request


def _request(xff: str | None, *, peer: str = "10.0.0.9") -> Request:
    headers = []
    if xff is not None:
        headers.append((b"x-forwarded-for", xff.encode()))
    scope = {
        "type": "http",
        "headers": headers,
        "client": (peer, 12345),
    }
    return Request(scope)


def _patch_hops(hops: int):
    # Both call sites import get_settings lazily from app.settings, so patch
    # it at the source. Returns the same patcher twice so the existing
    # two-tuple unpacking in the tests keeps working.
    settings = MagicMock()
    settings.trusted_proxy_hops = hops
    p = patch("app.settings.get_settings", return_value=settings)
    return (p, p)


class TestGetClientIp:
    def test_single_trusted_hop_takes_rightmost(self) -> None:
        # Real client IP is appended last by the trusted proxy (Cloud Run).
        rl, _ = _patch_hops(1)
        with rl:
            assert get_client_ip(_request("203.0.113.7")) == "203.0.113.7"

    def test_spoofed_leftmost_entry_is_ignored(self) -> None:
        # Attacker prepends a forged IP; the proxy appends the real one.
        rl, _ = _patch_hops(1)
        with rl:
            ip = get_client_ip(_request("1.2.3.4, 203.0.113.7"))
        assert ip == "203.0.113.7"

    def test_two_hops_reads_second_from_right(self) -> None:
        rl, _ = _patch_hops(2)
        with rl:
            ip = get_client_ip(_request("1.2.3.4, 203.0.113.7, 35.0.0.1"))
        assert ip == "203.0.113.7"

    def test_hops_clamped_to_available_entries(self) -> None:
        rl, _ = _patch_hops(5)
        with rl:
            assert get_client_ip(_request("203.0.113.7")) == "203.0.113.7"

    def test_no_header_falls_back_to_peer(self) -> None:
        rl, _ = _patch_hops(1)
        with rl:
            assert get_client_ip(_request(None, peer="10.0.0.9")) == "10.0.0.9"


class TestExtractRequestContext:
    def test_audit_ip_uses_trusted_entry_not_spoofed_leftmost(self) -> None:
        _, rc = _patch_hops(1)
        with rc:
            ip, _ua = extract_request_context(_request("1.2.3.4, 203.0.113.7"))
        assert ip == "203.0.113.7"

    def test_none_request_returns_none(self) -> None:
        assert extract_request_context(None) == (None, None)
