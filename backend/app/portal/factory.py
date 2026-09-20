# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Wiring: settings into a service, and the seam a deployment extends.

Everything configurable fails CLOSED. An empty signing key raises out of
:class:`~app.portal.service.PortalAuthService`; an unset portal origin or an
unwired channel surfaces as
:class:`~app.portal.delivery.DeliveryNotConfiguredError`, which the routes
turn into a 503. None of it degrades into a half-working invitation.

**The two channels are a registration seam, not a fixed list.** The engine
ships an SMTP email adapter and two development-only step-up gateways (see
:mod:`app.portal.adapters`). A deployment with a real provider calls
:func:`register_invite_delivery` / :func:`register_sms_gateway` at startup
and the routes pick it up with no change to them. That is the whole
extension mechanism: nothing outside this module ever names a provider.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING
from urllib.parse import quote

from ..services.email_sender import email_sender_from_settings
from ..settings import get_settings
from .adapters import CapturingSmsGateway, ConsoleSmsGateway, SmtpInviteDelivery
from .delivery import (
    DeliveryNotConfigured,
    DeliveryNotConfiguredError,
    PortalInviteDelivery,
    SmsGateway,
)
from .service import PortalAuthConfig, PortalAuthService

if TYPE_CHECKING:
    from collections.abc import Callable

    from .store import PortalAuthStore, PortalSessionStore

#: Where a magic link lands: the practice's own portal page, which is the page
#: the patient is going to end up on anyway. The shell reads the practice from
#: this path segment, so the link needs no separate landing page and nothing
#: downstream has to carry a practice around to find out where to send
#: somebody.
#:
#: The token rides in the URL **fragment**, never the query string. A fragment
#: is not sent to a server, so the credential stays out of access logs, out of
#: ``Referer`` headers on anything the page later loads, and out of every proxy
#: in between; the page reads it off ``location.hash`` and posts it to the
#: redeem endpoint. An ``?invite=`` link would be logged by every hop that
#: handled it.
PORTAL_PRACTICE_PATH = "/portal/{slug}"
INVITE_FRAGMENT_KEY = "invite"

_invite_delivery_factory: Callable[[], PortalInviteDelivery] | None = None
_sms_gateway_factory: Callable[[], SmsGateway] | None = None


def register_invite_delivery(factory: Callable[[], PortalInviteDelivery]) -> None:
    """Supply the adapter that emails magic links, replacing the default.

    Called once at startup. The factory runs per request, so it may read
    settings that change under it without being re-registered.
    """
    global _invite_delivery_factory  # noqa: PLW0603
    _invite_delivery_factory = factory


def register_sms_gateway(factory: Callable[[], SmsGateway]) -> None:
    """Supply the gateway that texts step-up codes, replacing the default."""
    global _sms_gateway_factory  # noqa: PLW0603
    _sms_gateway_factory = factory


def reset_delivery_registrations() -> None:
    """Drop both registrations, restoring the settings-driven defaults.

    For tests, which would otherwise leak a registered adapter from one
    case into every case after it through these module-level slots.
    """
    global _invite_delivery_factory, _sms_gateway_factory  # noqa: PLW0603
    _invite_delivery_factory = None
    _sms_gateway_factory = None


def _default_now() -> int:
    return int(time.time())


def portal_config_from_settings() -> PortalAuthConfig:
    settings = get_settings()
    return PortalAuthConfig(
        signing_key=settings.portal_token_signing_key.get_secret_value(),
        session_max_lifetime_seconds=settings.portal_session_max_lifetime_seconds,
    )


def invite_delivery_from_settings() -> PortalInviteDelivery:
    """The registered adapter, the configured one, or the refusing stub."""
    if _invite_delivery_factory is not None:
        return _invite_delivery_factory()
    settings = get_settings()
    if settings.portal_invite_delivery == "smtp":
        return SmtpInviteDelivery(sender=email_sender_from_settings(settings))
    return DeliveryNotConfigured("email")


def sms_gateway_from_settings() -> SmsGateway:
    """The registered gateway, the configured one, or the refusing stub.

    ``console`` and ``capture`` both put the step-up code somewhere other
    than the patient's phone, so both are refused outside a development
    environment — and refused by returning the stub rather than by raising,
    so a misconfigured deployment answers an honest 503 on the invite route
    instead of failing to boot.
    """
    if _sms_gateway_factory is not None:
        return _sms_gateway_factory()
    settings = get_settings()
    choice = settings.portal_sms_gateway
    if choice == "none":
        return DeliveryNotConfigured("SMS")
    if not settings.is_development:
        return DeliveryNotConfigured("SMS")
    if choice == "console":
        return ConsoleSmsGateway()
    return CapturingSmsGateway(base_url=settings.portal_sms_capture_url)


def build_invite_link(*, slug: str, token: str) -> str:
    """The magic link a patient clicks, or raise if there is nowhere to point.

    ``slug`` is the practice's own portal address, so the link opens the page
    the patient belongs on and the shell knows which practice it is serving
    before it asks anything. See :data:`PORTAL_PRACTICE_PATH` for why the
    token is in the fragment rather than the query string.
    """
    base = get_settings().portal_web_base_url.rstrip("/")
    if not base:
        raise DeliveryNotConfiguredError(
            "No portal web origin is configured; there is no link to mint."
        )
    path = PORTAL_PRACTICE_PATH.format(slug=quote(slug, safe=""))
    return f"{base}{path}#{INVITE_FRAGMENT_KEY}={quote(token, safe='')}"


def build_portal_auth_service(
    *,
    store: PortalAuthStore | None = None,
    sessions: PortalSessionStore | None = None,
    sms: SmsGateway | None = None,
) -> PortalAuthService:
    return PortalAuthService(
        config=portal_config_from_settings(),
        store=store,
        sessions=sessions,
        sms=sms or sms_gateway_from_settings(),
        now=_default_now,
    )


def get_invite_delivery() -> PortalInviteDelivery:
    """FastAPI dependency — the indirection route tests override."""
    return invite_delivery_from_settings()


def get_sms_gateway() -> SmsGateway:
    """FastAPI dependency — the indirection route tests override."""
    return sms_gateway_from_settings()
