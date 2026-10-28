# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""The web app's server may forward the end user's IP and agent for audit rows.

When the web app renders a page on its server, the server calls the backend
itself, so the request's address and agent are the server's. With
``Settings.frontend_forward_secret`` set, the server forwards the user's in
dedicated headers, proven by the shared secret. Without a matching secret the
headers are ignored, since any caller can set a header.
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import TYPE_CHECKING
from unittest.mock import patch

import pytest
from app.request_context import (
    FORWARD_AUTH_HEADER,
    FORWARDED_CLIENT_IP_HEADER,
    FORWARDED_CLIENT_UA_HEADER,
    MAX_USER_AGENT_LENGTH,
    extract_request_context,
)
from app.settings import get_settings
from pydantic import SecretStr
from starlette.requests import Request

if TYPE_CHECKING:
    from collections.abc import Iterator

_SERVER_IP = "203.0.113.50"
_SERVER_UA = "node"
_USER_IP = "198.51.100.23"
_USER_UA = "Mozilla/5.0 (Macintosh) Safari/605.1.15"
_SECRET = "s3cret-forwarding-value"


def _request(extra: dict[str, str]) -> Request:
    headers = {"x-forwarded-for": _SERVER_IP, "user-agent": _SERVER_UA, **extra}
    scope = {
        "type": "http",
        "headers": [(k.lower().encode(), v.encode()) for k, v in headers.items()],
        "client": ("10.0.0.9", 12345),
    }
    return Request(scope)


def _forwarding(auth: str = _SECRET, ip: str = _USER_IP, ua: str = _USER_UA) -> dict[str, str]:
    return {
        FORWARD_AUTH_HEADER: auth,
        FORWARDED_CLIENT_IP_HEADER: ip,
        FORWARDED_CLIENT_UA_HEADER: ua,
    }


@contextmanager
def _configured(secret: str) -> Iterator[None]:
    settings = get_settings().model_copy(
        update={"frontend_forward_secret": SecretStr(secret), "trusted_proxy_hops": 1}
    )
    with patch("app.settings.get_settings", return_value=settings):
        yield


class TestForwardedClient:
    def test_matching_secret_uses_forwarded_ip_and_agent(self) -> None:
        with _configured(_SECRET):
            assert extract_request_context(_request(_forwarding())) == (_USER_IP, _USER_UA)

    def test_ipv6_is_accepted_and_normalised(self) -> None:
        with _configured(_SECRET):
            ip, _ = extract_request_context(_request(_forwarding(ip="2001:DB8::0:1")))
        assert ip == "2001:db8::1"

    @pytest.mark.parametrize("presented", ["wrong", "", _SECRET + "x", _SECRET[:-1]])
    def test_wrong_secret_ignores_the_headers(self, presented: str) -> None:
        with _configured(_SECRET):
            result = extract_request_context(_request(_forwarding(auth=presented)))
        assert result == (_SERVER_IP, _SERVER_UA)

    def test_missing_secret_header_ignores_the_headers(self) -> None:
        forwarded = _forwarding()
        del forwarded[FORWARD_AUTH_HEADER]
        with _configured(_SECRET):
            assert extract_request_context(_request(forwarded)) == (_SERVER_IP, _SERVER_UA)

    def test_unset_setting_ignores_the_headers(self) -> None:
        with _configured(""):
            assert extract_request_context(_request(_forwarding())) == (_SERVER_IP, _SERVER_UA)

    def test_unset_setting_ignores_even_an_empty_presented_secret(self) -> None:
        with _configured(" , "):
            result = extract_request_context(_request(_forwarding(auth="")))
        assert result == (_SERVER_IP, _SERVER_UA)

    @pytest.mark.parametrize("bad_ip", ["", "not-an-ip", "1.2.3.4, 5.6.7.8", "999.1.1.1"])
    def test_invalid_ip_ignores_the_headers(self, bad_ip: str) -> None:
        with _configured(_SECRET):
            result = extract_request_context(_request(_forwarding(ip=bad_ip)))
        assert result == (_SERVER_IP, _SERVER_UA)

    def test_missing_ip_ignores_the_headers(self) -> None:
        forwarded = _forwarding()
        del forwarded[FORWARDED_CLIENT_IP_HEADER]
        with _configured(_SECRET):
            assert extract_request_context(_request(forwarded)) == (_SERVER_IP, _SERVER_UA)

    def test_long_agent_is_capped(self) -> None:
        with _configured(_SECRET):
            _, ua = extract_request_context(_request(_forwarding(ua="A" * 5000)))
        assert ua == "A" * MAX_USER_AGENT_LENGTH

    def test_missing_agent_is_recorded_as_none(self) -> None:
        forwarded = _forwarding()
        del forwarded[FORWARDED_CLIENT_UA_HEADER]
        with _configured(_SECRET):
            assert extract_request_context(_request(forwarded)) == (_USER_IP, None)

    @pytest.mark.parametrize("presented", ["new-secret", "old-secret"])
    def test_either_secret_in_a_rotation_is_accepted(self, presented: str) -> None:
        with _configured("new-secret, old-secret"):
            result = extract_request_context(_request(_forwarding(auth=presented)))
        assert result == (_USER_IP, _USER_UA)

    def test_rotation_list_does_not_accept_a_joined_value(self) -> None:
        with _configured("new-secret,old-secret"):
            result = extract_request_context(_request(_forwarding(auth="new-secret,old-secret")))
        assert result == (_SERVER_IP, _SERVER_UA)

    def test_rejection_logs_no_values(self, caplog: pytest.LogCaptureFixture) -> None:
        with _configured(_SECRET), caplog.at_level("WARNING", logger="app.request_context"):
            extract_request_context(_request(_forwarding(auth="attacker-value")))
        assert caplog.records
        text = caplog.text
        for value in ("attacker-value", _SECRET, _USER_IP, _USER_UA):
            assert value not in text
