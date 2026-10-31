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


def _patch_hops(hops: int, *, trusted_proxy_ips: str = "*"):
    # Both call sites import get_settings lazily from app.settings, so patch
    # it at the source. Returns the same patcher twice so the existing
    # two-tuple unpacking in the tests keeps working.
    settings = MagicMock()
    settings.trusted_proxy_hops = hops
    settings.trusted_proxy_ips = trusted_proxy_ips
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


class TestTrustedProxyGate:
    """The header is only read when the immediate peer is a listed proxy.

    With no proxy trusted (the self-host default when the app port is
    reachable directly), a caller sending a fresh single-entry header per
    request would otherwise get a fresh limiter key per request.
    """

    def test_untrusted_peer_is_keyed_on_its_own_address(self) -> None:
        rl, _ = _patch_hops(1, trusted_proxy_ips="")
        with rl:
            ip = get_client_ip(_request("203.0.113.7", peer="198.51.100.4"))
        assert ip == "198.51.100.4"

    def test_untrusted_peer_cannot_rotate_keys_by_rotating_the_header(self) -> None:
        rl, _ = _patch_hops(1, trusted_proxy_ips="")
        with rl:
            keys = {
                get_client_ip(_request(f"203.0.113.{n}", peer="198.51.100.4")) for n in range(5)
            }
        assert keys == {"198.51.100.4"}

    def test_peer_not_in_list_is_keyed_on_its_own_address(self) -> None:
        rl, _ = _patch_hops(1, trusted_proxy_ips="172.30.0.10")
        with rl:
            ip = get_client_ip(_request("203.0.113.7", peer="198.51.100.4"))
        assert ip == "198.51.100.4"

    def test_listed_peer_yields_the_forwarded_client(self) -> None:
        rl, _ = _patch_hops(1, trusted_proxy_ips="172.30.0.10")
        with rl:
            ip = get_client_ip(_request("1.2.3.4, 203.0.113.7", peer="172.30.0.10"))
        assert ip == "203.0.113.7"

    def test_listed_peer_keeps_hop_semantics(self) -> None:
        rl, _ = _patch_hops(2, trusted_proxy_ips="172.30.0.10, 172.30.0.11")
        with rl:
            ip = get_client_ip(_request("1.2.3.4, 203.0.113.7, 35.0.0.1", peer="172.30.0.11"))
        assert ip == "203.0.113.7"

    def test_wildcard_trusts_any_peer(self) -> None:
        rl, _ = _patch_hops(1, trusted_proxy_ips="*")
        with rl:
            ip = get_client_ip(_request("203.0.113.7", peer="198.51.100.4"))
        assert ip == "203.0.113.7"

    def test_no_peer_address_is_never_trusted(self) -> None:
        rl, _ = _patch_hops(1, trusted_proxy_ips="172.30.0.10")
        request = Request({"type": "http", "headers": [(b"x-forwarded-for", b"203.0.113.7")]})
        with rl:
            assert get_client_ip(request) == "unknown"


class TestExtractRequestContext:
    def test_audit_ip_uses_trusted_entry_not_spoofed_leftmost(self) -> None:
        _, rc = _patch_hops(1)
        with rc:
            ip, _ua = extract_request_context(_request("1.2.3.4, 203.0.113.7"))
        assert ip == "203.0.113.7"

    def test_untrusted_peer_cannot_name_the_audited_address(self) -> None:
        _, rc = _patch_hops(1, trusted_proxy_ips="")
        with rc:
            ip, _ua = extract_request_context(_request("203.0.113.7", peer="198.51.100.4"))
        assert ip == "198.51.100.4"

    def test_peer_not_in_list_is_audited_as_itself(self) -> None:
        _, rc = _patch_hops(1, trusted_proxy_ips="172.30.0.10")
        with rc:
            ip, _ua = extract_request_context(_request("203.0.113.7", peer="198.51.100.4"))
        assert ip == "198.51.100.4"

    def test_listed_peer_yields_the_right_hand_hop(self) -> None:
        _, rc = _patch_hops(1, trusted_proxy_ips="172.30.0.10")
        with rc:
            ip, _ua = extract_request_context(_request("1.2.3.4, 203.0.113.7", peer="172.30.0.10"))
        assert ip == "203.0.113.7"

    def test_no_peer_address_is_audited_as_none(self) -> None:
        _, rc = _patch_hops(1, trusted_proxy_ips="172.30.0.10")
        request = Request({"type": "http", "headers": [(b"x-forwarded-for", b"203.0.113.7")]})
        with rc:
            assert extract_request_context(request) == (None, None)

    def test_none_request_returns_none(self) -> None:
        assert extract_request_context(None) == (None, None)
