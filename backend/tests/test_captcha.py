# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Unit tests for the pluggable CAPTCHA verification backends.

The Turnstile backend is exercised against a fake httpx client (a test
seam, mirroring the client_factory pattern in test_email_sender.py) — no
network, no real socket.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

import httpx
from app.services.captcha import (
    CaptchaVerifier,
    NoneCaptchaVerifier,
    TurnstileVerifier,
    captcha_verifier_from_settings,
)
from app.settings import Settings

if TYPE_CHECKING:
    import pytest

_TOKEN = "the-turnstile-response-token"
_SECRET = "the-turnstile-secret-key"


class _FakeResponse:
    def __init__(self, *, json_body: dict[str, Any] | None = None, status_code: int = 200) -> None:
        self._json_body = json_body if json_body is not None else {}
        self.status_code = status_code

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            request = httpx.Request(
                "POST", "https://challenges.cloudflare.com/turnstile/v0/siteverify"
            )
            response = httpx.Response(self.status_code, request=request)
            raise httpx.HTTPStatusError("boom", request=request, response=response)

    def json(self) -> dict[str, Any]:
        return self._json_body


class _FakeClient:
    """Records the single POST it received; returns a canned response or raises."""

    def __init__(
        self,
        *,
        response: _FakeResponse | None = None,
        raise_exc: Exception | None = None,
    ) -> None:
        self._response = response
        self._raise_exc = raise_exc
        self.calls: list[dict[str, Any]] = []

    def post(self, url: str, data: dict[str, Any]) -> _FakeResponse:
        self.calls.append({"url": url, "data": data})
        if self._raise_exc is not None:
            raise self._raise_exc
        assert self._response is not None
        return self._response


def _verifier(*, fake: _FakeClient) -> TurnstileVerifier:
    return TurnstileVerifier(
        site_key="the-turnstile-site-key",
        secret_key=_SECRET,
        client_factory=lambda: fake,
    )


def _no_secrets(caplog: pytest.LogCaptureFixture) -> None:
    haystacks = [record.getMessage() for record in caplog.records]
    for needle in (_TOKEN, _SECRET):
        assert all(needle not in text for text in haystacks)


class TestNoneCaptchaVerifier:
    def test_site_key_is_none(self) -> None:
        assert NoneCaptchaVerifier().site_key is None

    def test_verify_always_true(self) -> None:
        assert NoneCaptchaVerifier().verify(None, None) is True


class TestTurnstileVerifier:
    def test_verify_posts_expected_fields_only(self) -> None:
        fake = _FakeClient(response=_FakeResponse(json_body={"success": True}))
        _verifier(fake=fake).verify(_TOKEN, "203.0.113.7")
        assert len(fake.calls) == 1
        call = fake.calls[0]
        assert call["url"] == "https://challenges.cloudflare.com/turnstile/v0/siteverify"
        assert call["data"] == {"secret": _SECRET, "response": _TOKEN, "remoteip": "203.0.113.7"}

    def test_verify_omits_remoteip_when_none(self) -> None:
        fake = _FakeClient(response=_FakeResponse(json_body={"success": True}))
        _verifier(fake=fake).verify(_TOKEN, None)
        assert fake.calls[0]["data"] == {"secret": _SECRET, "response": _TOKEN}

    def test_success_true_returns_true(self) -> None:
        fake = _FakeClient(response=_FakeResponse(json_body={"success": True}))
        assert _verifier(fake=fake).verify(_TOKEN, "203.0.113.7") is True

    def test_success_false_returns_false(self) -> None:
        fake = _FakeClient(response=_FakeResponse(json_body={"success": False}))
        assert _verifier(fake=fake).verify(_TOKEN, "203.0.113.7") is False

    def test_connect_error_fails_open(self, caplog: pytest.LogCaptureFixture) -> None:
        fake = _FakeClient(raise_exc=httpx.ConnectError("connection refused"))
        with caplog.at_level(logging.WARNING):
            assert _verifier(fake=fake).verify(_TOKEN, "203.0.113.7") is True
        assert any(record.levelno == logging.WARNING for record in caplog.records)
        _no_secrets(caplog)

    def test_timeout_fails_open(self, caplog: pytest.LogCaptureFixture) -> None:
        fake = _FakeClient(raise_exc=httpx.TimeoutException("timed out"))
        with caplog.at_level(logging.WARNING):
            assert _verifier(fake=fake).verify(_TOKEN, "203.0.113.7") is True
        _no_secrets(caplog)

    def test_server_error_fails_open(self, caplog: pytest.LogCaptureFixture) -> None:
        fake = _FakeClient(response=_FakeResponse(status_code=500))
        with caplog.at_level(logging.WARNING):
            assert _verifier(fake=fake).verify(_TOKEN, "203.0.113.7") is True
        _no_secrets(caplog)

    def test_site_key_is_set(self) -> None:
        fake = _FakeClient(response=_FakeResponse(json_body={"success": True}))
        assert _verifier(fake=fake).site_key == "the-turnstile-site-key"


def _settings(**overrides: Any) -> Settings:
    return Settings(
        database_url="postgresql://test:test@localhost:5432/test",
        **overrides,
    )


class TestCaptchaVerifierFromSettings:
    def test_defaults_to_none(self) -> None:
        verifier: CaptchaVerifier = captcha_verifier_from_settings(_settings())
        assert isinstance(verifier, NoneCaptchaVerifier)

    def test_turnstile_backend_plumbs_settings(self) -> None:
        verifier = captcha_verifier_from_settings(
            _settings(
                captcha_provider="turnstile",
                turnstile_site_key="the-turnstile-site-key",
                turnstile_secret_key=_SECRET,
            )
        )
        assert isinstance(verifier, TurnstileVerifier)
        assert verifier.site_key == "the-turnstile-site-key"
        assert verifier._secret_key == _SECRET
