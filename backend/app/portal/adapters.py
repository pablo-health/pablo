# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""The delivery adapters the engine ships.

One per channel, so a deployment that has an SMTP server and a development
environment can walk the whole flow without writing code:

* :class:`SmtpInviteDelivery` emails the magic link through the engine's
  existing SMTP sender, which means it inherits the STARTTLS handling, the
  timeout and the scoped certificate store already proven there.
* :class:`ConsoleSmsGateway` writes the message to the log, and
  :class:`CapturingSmsGateway` posts it to a service that keeps it. Both put
  the second factor somewhere a person other than the patient can read it,
  which is the whole reason they are useful and exactly why
  :func:`app.portal.factory.sms_gateway_from_settings` refuses to build
  either outside a development environment.

A deployment that texts real people registers its own gateway through
:mod:`app.portal.factory`. That seam is the extension point; this module is
the floor, not the ceiling.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

import httpx

from ..services.email_sender import EmailSender, OutboundEmail
from .delivery import DeliveryNotConfiguredError
from .invite_email import DEFAULT_TEMPLATE, InviteContext, describe_duration, render
from .service import PortalAuthConfig

logger = logging.getLogger(__name__)

#: What the patient reads when the practice has not worded its own: the
#: engine's default template (``app.portal.invite_email``) with the link in.
#: It names neither the practice nor the client — anyone who reaches the
#: inbox would learn the recipient is in care somewhere, which is not this
#: email's to disclose.
_DEFAULT_INVITE = render(
    DEFAULT_TEMPLATE,
    InviteContext(
        portal_link="{link}",
        client_first_name="",
        practice_name="",
        forms=[],
        # The engine's default lifetime. Built once at import, so it cannot
        # read settings; a deployment that changes the lifetime and wants it
        # stated sends practice wording through ``send_rendered_invite``.
        link_expiry=describe_duration(PortalAuthConfig(signing_key="").invite_ttl_seconds),
    ),
)
INVITE_SUBJECT = _DEFAULT_INVITE.subject
INVITE_BODY = _DEFAULT_INVITE.text


@dataclass
class SmtpInviteDelivery:
    """Emails the magic link through an :class:`EmailSender`.

    Takes the sender rather than SMTP settings so this class knows nothing
    about ``smtplib``: what it adds is the message, and the fact that a
    sender which cannot deliver is a refusal rather than a silent drop.
    """

    sender: EmailSender

    def check_ready(self) -> None:
        if not self.sender.can_deliver:
            raise DeliveryNotConfiguredError(
                "No email delivery is configured for portal invitations."
            )

    def send_invite(self, *, to_email: str, link: str) -> None:
        self.send_rendered_invite(
            to_email=to_email, subject=INVITE_SUBJECT, text=INVITE_BODY.format(link=link)
        )

    def send_rendered_invite(self, *, to_email: str, subject: str, text: str) -> None:
        """Send practice-written wording. SMTP carries whatever the
        deployment's own mail server is trusted with, so this adapter
        offers the editor (see ``RenderedInviteDelivery``)."""
        self.check_ready()
        self.sender.send(
            OutboundEmail(to=to_email, subject=subject, text=text, kind="portal_invite")
        )


class ConsoleSmsGateway:
    """Writes the message to the log instead of texting it.

    Development only, and the log line is the delivery: whoever can read the
    log can complete the step-up. :func:`app.portal.factory
    .sms_gateway_from_settings` is what keeps that confined to a development
    environment.
    """

    def check_ready(self) -> None:
        return None

    def send(self, *, to: str, body: str) -> None:
        # The recipient is deliberately not logged beside the code: the pair
        # is a working credential, and the code alone is not.
        logger.info("portal step-up code (development console gateway): %s", body)


@dataclass
class CapturingSmsGateway:
    """Posts each message to a service that keeps it, for a test to read.

    The same shape as the other stand-ins the local stack runs: the engine
    speaks to a configured origin, and what listens there is a fake that
    records instead of delivering. Development only, for the same reason as
    :class:`ConsoleSmsGateway` — it hands the second factor to anything that
    can reach that service.
    """

    base_url: str
    client: httpx.Client = field(default_factory=lambda: httpx.Client(timeout=10.0))

    def check_ready(self) -> None:
        if not self.base_url:
            raise DeliveryNotConfiguredError(
                "No capture origin is configured for portal step-up codes."
            )

    def send(self, *, to: str, body: str) -> None:
        self.check_ready()
        response = self.client.post(
            f"{self.base_url.rstrip('/')}/_fake/sms",
            json={"to": to, "body": body},
        )
        response.raise_for_status()
