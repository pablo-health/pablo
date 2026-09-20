# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.
"""The two channels an invitation travels on: adapters, selection, and the
registration seam a deployment extends.

Three things are worth more than the rest here:

* **Not configured refuses.** Both ports answer ``DeliveryNotConfigured``
  rather than dropping a message, because a dropped invitation looks
  delivered from the clinician's side.
* **The development-only gateways stay in development.** ``console`` and
  ``capture`` both hand the step-up code to somebody who is not the patient.
  The settings selector is the only thing standing between that and a
  production deployment, so it is pinned.
* **A registered adapter wins.** The seam is how a deployment ships a real
  provider without touching a route.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

import httpx
import pytest
from app.portal import factory
from app.portal.adapters import (
    INVITE_SUBJECT,
    CapturingSmsGateway,
    ConsoleSmsGateway,
    SmtpInviteDelivery,
)
from app.portal.delivery import (
    CapturingInviteDelivery,
    DeliveryNotConfigured,
    DeliveryNotConfiguredError,
    FakeSmsGateway,
)
from app.services.email_sender import InMemoryEmailSender, NoneEmailSender
from app.settings import get_settings

if TYPE_CHECKING:
    from collections.abc import Iterator

LINK = "https://portal.example.test/portal/redeem#token=abc.def.ghi"


@pytest.fixture(autouse=True)
def _clean_settings_and_registrations() -> Iterator[None]:
    """Settings are cached and the registration slots are module-level; a
    test that changes either must not charge it to the next one."""
    get_settings.cache_clear()
    factory.reset_delivery_registrations()
    yield
    get_settings.cache_clear()
    factory.reset_delivery_registrations()


# ---------------------------------------------------------------------------
# The refusing stub
# ---------------------------------------------------------------------------


def test_the_stub_refuses_on_both_ports() -> None:
    """One class covers both, so a half-configured deployment cannot send one
    factor and drop the other."""
    stub = DeliveryNotConfigured("email")

    with pytest.raises(DeliveryNotConfiguredError):
        stub.check_ready()
    with pytest.raises(DeliveryNotConfiguredError):
        stub.send_invite(to_email="patient@example.test", link=LINK)
    with pytest.raises(DeliveryNotConfiguredError):
        stub.send(to="+15005550006", body="123456")


def test_the_test_doubles_record_instead_of_sending() -> None:
    delivery = CapturingInviteDelivery()
    sms = FakeSmsGateway()

    delivery.send_invite(to_email="patient@example.test", link=LINK)
    sms.send(to="+15005550006", body="123456")

    assert delivery.sent[0].link == LINK
    assert sms.sent[0].to == "+15005550006"


# ---------------------------------------------------------------------------
# The SMTP adapter
# ---------------------------------------------------------------------------


def test_smtp_delivery_sends_the_link_and_nothing_about_the_practice() -> None:
    """The email says where to go. Naming the practice would tell anyone who
    reaches the inbox that the recipient is in care somewhere."""
    sender = InMemoryEmailSender()

    SmtpInviteDelivery(sender=sender).send_invite(to_email="patient@example.test", link=LINK)

    assert len(sender.sent) == 1
    message = sender.sent[0]
    assert message.to == "patient@example.test"
    assert message.subject == INVITE_SUBJECT
    assert LINK in message.text
    assert message.kind == "portal_invite"


def test_smtp_delivery_refuses_when_the_sender_cannot_deliver() -> None:
    """``NoneEmailSender`` accepts and drops. Checked before anything is
    minted, so the route answers 503 rather than burning a challenge."""
    delivery = SmtpInviteDelivery(sender=NoneEmailSender())

    with pytest.raises(DeliveryNotConfiguredError):
        delivery.check_ready()
    with pytest.raises(DeliveryNotConfiguredError):
        delivery.send_invite(to_email="patient@example.test", link=LINK)


# ---------------------------------------------------------------------------
# The development-only step-up gateways
# ---------------------------------------------------------------------------


def test_console_gateway_writes_the_message_to_the_log(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.INFO, logger="app.portal.adapters"):
        ConsoleSmsGateway().send(to="+15005550006", body="Your code is 123456")

    assert "Your code is 123456" in caplog.text


def test_capture_gateway_posts_to_the_configured_origin() -> None:
    captured: dict[str, httpx.Request] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["request"] = request
        return httpx.Response(204)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    CapturingSmsGateway(base_url="http://fake-sms:8026/", client=client).send(
        to="+15005550006", body="Your code is 123456"
    )

    request = captured["request"]
    # A trailing slash on the origin still builds one slash, not two.
    assert str(request.url) == "http://fake-sms:8026/_fake/sms"
    assert request.method == "POST"


def test_capture_gateway_refuses_without_an_origin() -> None:
    with pytest.raises(DeliveryNotConfiguredError):
        CapturingSmsGateway(base_url="").check_ready()


# ---------------------------------------------------------------------------
# Selection from settings
# ---------------------------------------------------------------------------


def _configured(monkeypatch: pytest.MonkeyPatch, env: dict[str, str]) -> None:
    """Set an environment and rebuild the cached settings from it."""
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    get_settings.cache_clear()


def test_email_defaults_to_the_refusing_stub(monkeypatch: pytest.MonkeyPatch) -> None:
    _configured(monkeypatch, {"PORTAL_INVITE_DELIVERY": "none"})

    assert isinstance(factory.invite_delivery_from_settings(), DeliveryNotConfigured)


def test_email_selects_the_smtp_adapter(monkeypatch: pytest.MonkeyPatch) -> None:
    _configured(
        monkeypatch,
        {
            "PORTAL_INVITE_DELIVERY": "smtp",
            "EMAIL_BACKEND": "smtp",
            "SMTP_HOST": "mail.example.test",
            "SMTP_FROM": "portal@example.test",
        },
    )

    assert isinstance(factory.invite_delivery_from_settings(), SmtpInviteDelivery)


def test_step_up_defaults_to_the_refusing_stub(monkeypatch: pytest.MonkeyPatch) -> None:
    _configured(monkeypatch, {"PORTAL_SMS_GATEWAY": "none"})

    assert isinstance(factory.sms_gateway_from_settings(), DeliveryNotConfigured)


@pytest.mark.parametrize(
    ("choice", "expected"),
    [("console", ConsoleSmsGateway), ("capture", CapturingSmsGateway)],
)
def test_the_development_gateways_are_available_in_development(
    monkeypatch: pytest.MonkeyPatch, choice: str, expected: type
) -> None:
    _configured(
        monkeypatch,
        {
            "ENVIRONMENT": "development",
            "PORTAL_SMS_GATEWAY": choice,
            "PORTAL_SMS_CAPTURE_URL": "http://fake-sms:8026",
        },
    )

    assert isinstance(factory.sms_gateway_from_settings(), expected)


@pytest.mark.parametrize("environment", ["staging", "production"])
@pytest.mark.parametrize("choice", ["console", "capture"])
def test_the_development_gateways_are_refused_outside_development(
    monkeypatch: pytest.MonkeyPatch, environment: str, choice: str
) -> None:
    """Both hand the step-up code to someone who is not the patient — a log
    reader, or anything that can reach the capture service. This selector is
    the only thing keeping that out of a deployment that serves real people,
    so it refuses by returning the stub: the invite route then answers an
    honest 503 rather than the process failing to boot.
    """
    _configured(
        monkeypatch,
        {
            "ENVIRONMENT": environment,
            "PORTAL_SMS_GATEWAY": choice,
            "PORTAL_SMS_CAPTURE_URL": "http://fake-sms:8026",
        },
    )

    gateway = factory.sms_gateway_from_settings()

    assert isinstance(gateway, DeliveryNotConfigured)
    with pytest.raises(DeliveryNotConfiguredError):
        gateway.check_ready()


# ---------------------------------------------------------------------------
# The registration seam
# ---------------------------------------------------------------------------


def test_a_registered_adapter_wins_over_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    """How a deployment ships a real provider: register at startup, change no
    route."""
    _configured(monkeypatch, {"PORTAL_INVITE_DELIVERY": "none", "PORTAL_SMS_GATEWAY": "none"})
    delivery, sms = CapturingInviteDelivery(), FakeSmsGateway()

    factory.register_invite_delivery(lambda: delivery)
    factory.register_sms_gateway(lambda: sms)

    assert factory.invite_delivery_from_settings() is delivery
    assert factory.sms_gateway_from_settings() is sms


def test_resetting_the_registrations_restores_the_settings_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configured(monkeypatch, {"PORTAL_SMS_GATEWAY": "none"})
    factory.register_sms_gateway(FakeSmsGateway)

    factory.reset_delivery_registrations()

    assert isinstance(factory.sms_gateway_from_settings(), DeliveryNotConfigured)


# ---------------------------------------------------------------------------
# The magic link
# ---------------------------------------------------------------------------


def test_the_link_carries_the_token_in_the_fragment(monkeypatch: pytest.MonkeyPatch) -> None:
    """A fragment is never sent to a server, so the credential stays out of
    access logs, ``Referer`` headers and every proxy in between."""
    _configured(monkeypatch, {"PORTAL_WEB_BASE_URL": "https://portal.example.test/"})

    link = factory.build_invite_link("abc.def.ghi")

    assert link == "https://portal.example.test/portal/redeem#token=abc.def.ghi"
    assert "?token=" not in link


def test_a_token_with_url_meta_characters_is_escaped(monkeypatch: pytest.MonkeyPatch) -> None:
    _configured(monkeypatch, {"PORTAL_WEB_BASE_URL": "https://portal.example.test"})

    link = factory.build_invite_link("a/b&c=d")

    assert link.endswith("#token=a%2Fb%26c%3Dd")


def test_no_origin_means_no_link_to_mint(monkeypatch: pytest.MonkeyPatch) -> None:
    _configured(monkeypatch, {"PORTAL_WEB_BASE_URL": ""})

    with pytest.raises(DeliveryNotConfiguredError):
        factory.build_invite_link("abc.def.ghi")


def test_the_service_factory_threads_settings_into_the_config(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Pins the wiring rather than the dataclass: a field nothing fills is
    the shape of bug that survives every unit test of the thing itself."""
    _configured(
        monkeypatch,
        {
            "PORTAL_TOKEN_SIGNING_KEY": "factory-test-signing-key",
            "PORTAL_SESSION_MAX_LIFETIME_SECONDS": "7200",
        },
    )

    config = factory.portal_config_from_settings()

    assert config.signing_key == "factory-test-signing-key"
    assert config.session_max_lifetime_seconds == 7200


def test_a_deployment_that_says_nothing_gets_neither_channel(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The floor: an engine with no configuration refuses both channels, so
    the invite route answers 503 instead of minting something undeliverable."""
    for name in ("PORTAL_INVITE_DELIVERY", "PORTAL_SMS_GATEWAY", "ENABLE_PATIENT_PORTAL"):
        monkeypatch.delenv(name, raising=False)
    get_settings.cache_clear()

    settings: Any = get_settings()

    assert settings.portal_invite_delivery == "none"
    assert settings.portal_sms_gateway == "none"
    # And the routes are not even mounted until a deployment says so.
    assert settings.enable_patient_portal is False
