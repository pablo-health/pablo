# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Pluggable CAPTCHA verification for the public booking write surface.

One interface, one backend plus a no-op default:

* :class:`NoneCaptchaVerifier` — the default. ``site_key`` is ``None`` (so
  the public card renders no widget) and ``verify`` always returns
  ``True``. This is what a bare deployment gets with no configuration.
* :class:`TurnstileVerifier` — Cloudflare Turnstile. Verifies a token
  against Cloudflare's ``siteverify`` endpoint with a synchronous
  ``httpx`` client — the booking POST this guards is a sync route, so a
  hanging vendor call can only pin the worker handling that one request,
  bounded by a short timeout.

Selection is a configuration change (``CAPTCHA_PROVIDER=none|turnstile``),
not a code change — see :func:`captcha_verifier_from_settings`. Callers
hold a :class:`CaptchaVerifier` and never touch the vendor API directly.

Fail-open: a network error, timeout, or non-2xx response from the vendor
logs a warning and lets the booking proceed — the booker cannot induce
that failure (the call is server-to-vendor), and the write rate limit and
email confirmation remain the abuse floor. A definitive vendor answer
(``success: false``) always refuses.

PHI note: the verify request carries only the token and the caller's IP —
never a name, an email, or a note. Log lines here carry outcome only,
never the token or the secret.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, Protocol

import httpx

from ..settings import Settings, get_settings

if TYPE_CHECKING:
    from collections.abc import Callable

logger = logging.getLogger(__name__)

_TURNSTILE_VERIFY_URL = "https://challenges.cloudflare.com/turnstile/v0/siteverify"
_VERIFY_TIMEOUT_SECONDS = 5.0


class CaptchaVerifier(Protocol):
    """CAPTCHA verification needed by the public booking write surface.

    ``site_key`` is public by definition — it is rendered into the
    booking card and the widget script — and is ``None`` when no
    provider is configured, telling the card to render no widget.
    """

    site_key: str | None

    def verify(self, token: str | None, remote_ip: str | None) -> bool:
        """Return whether ``token`` proves the request came from a human.

        ``token`` is ``None`` when the caller sent no ``X-Captcha-Token``
        header — always refused, indistinguishably from an invalid token.
        """
        ...


class NoneCaptchaVerifier:
    """Default backend: no provider configured, every request passes."""

    site_key: str | None = None

    def verify(
        self,
        token: str | None,  # noqa: ARG002 — args document the Protocol's shape
        remote_ip: str | None,  # noqa: ARG002
    ) -> bool:
        return True


class TurnstileVerifier:
    """Cloudflare Turnstile backend.

    ``client_factory`` is a test seam, mirroring the pattern in
    ``email_sender.py`` — production connects a real ``httpx.Client``
    lazily so importing this module never opens a socket.
    """

    def __init__(
        self,
        *,
        site_key: str,
        secret_key: str,
        client_factory: Callable[[], Any] | None = None,
    ) -> None:
        self.site_key: str | None = site_key
        self._secret_key = secret_key
        self._client_factory = client_factory

    def _client(self) -> Any:
        if self._client_factory is not None:
            return self._client_factory()
        return httpx.Client(timeout=_VERIFY_TIMEOUT_SECONDS)

    def verify(self, token: str | None, remote_ip: str | None) -> bool:
        data = {"secret": self._secret_key, "response": token or ""}
        if remote_ip is not None:
            data["remoteip"] = remote_ip

        client = self._client()
        try:
            response = client.post(_TURNSTILE_VERIFY_URL, data=data)
            response.raise_for_status()
            result = response.json()
        except (httpx.HTTPError, ValueError):
            logger.warning("turnstile verify unavailable, allowing booking through")
            return True
        return bool(result.get("success"))


def captcha_verifier_from_settings(settings: Settings) -> CaptchaVerifier:
    """Construct the configured backend. ``none`` (no-op) is the default."""
    if settings.captcha_provider == "turnstile":
        return TurnstileVerifier(
            site_key=settings.turnstile_site_key,
            secret_key=settings.turnstile_secret_key.get_secret_value(),
        )
    return NoneCaptchaVerifier()


def get_captcha_verifier() -> CaptchaVerifier:
    """FastAPI dependency — the configured backend for this deployment."""
    return captcha_verifier_from_settings(get_settings())
