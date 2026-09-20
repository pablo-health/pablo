# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""The two channels an invitation travels on, as ports.

Redemption needs two factors on two channels: the link arrives by email
(possession of the inbox), the code by text message (possession of the
phone). This module defines both ports, the test doubles that record instead
of sending, and the refusing stub a deployment gets when a channel is not
configured. The adapters that really deliver are in
:mod:`app.portal.adapters`; a deployment selects between them in settings,
and one that needs a different provider registers its own through
:mod:`app.portal.factory`.

**Not configured is a refusal, never a silent drop.**
:class:`DeliveryNotConfiguredError` is what an unwired channel raises, and
the invite route turns it into a 503 *before* anything is minted or sent. A
dropped invitation looks exactly like a delivered one from the clinician's
side, and the patient is the one who finds out.

Neither channel's payload carries anything clinical: the email is a link,
the text is a code. Both are credentials, so neither is ever written to a
log or returned in a response — they go to the patient and nowhere else.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


class DeliveryNotConfiguredError(RuntimeError):
    """No adapter is wired for this channel, so nothing was sent."""


class PortalInviteDelivery(Protocol):
    """Emails one magic link."""

    def send_invite(self, *, to_email: str, link: str) -> None:
        """Email one magic link. Raises on delivery failure."""
        ...

    def check_ready(self) -> None:
        """Raise :class:`DeliveryNotConfiguredError` if a send would fail
        for want of configuration.

        Exists so the invite route can refuse BEFORE the service texts a
        code and burns a challenge. Without it, an unconfigured email
        channel leaves the patient holding a code for a link that never
        arrives — a dead-end invitation rather than an honest 503.
        """


class SmsGateway(Protocol):
    """Texts one short message."""

    def send(self, *, to: str, body: str) -> None:
        """Deliver ``body`` to ``to`` (E.164). Raises on delivery failure."""
        ...

    def check_ready(self) -> None:
        """Raise if a send would fail for want of configuration.

        Mirrors :meth:`PortalInviteDelivery.check_ready`: the invite route
        asks BOTH channels before it mints anything, because an invitation
        whose second factor cannot be delivered is worse than no invitation.
        """


@dataclass
class SentInviteEmail:
    to_email: str
    link: str


@dataclass
class SentSms:
    to: str
    body: str


class CapturingInviteDelivery:
    """Records invitations instead of sending them. For tests.

    ``sent`` is the ordered log. Anything that reads a link back out of it
    is handling a credential, so this class belongs to test code and to
    deliberately fenced test surfaces — never to a request path.
    """

    def __init__(self) -> None:
        self.sent: list[SentInviteEmail] = []

    def send_invite(self, *, to_email: str, link: str) -> None:
        self.sent.append(SentInviteEmail(to_email=to_email, link=link))

    def check_ready(self) -> None:
        return None


class FakeSmsGateway:
    """In-memory gateway. ``sent`` is the ordered log of deliveries."""

    def __init__(self) -> None:
        self.sent: list[SentSms] = []

    def send(self, *, to: str, body: str) -> None:
        self.sent.append(SentSms(to=to, body=body))

    def check_ready(self) -> None:
        return None


class DeliveryNotConfigured:
    """The refusing stub, and the default on both channels.

    One class covers both ports so a half-configured deployment cannot send
    one factor and drop the other.
    """

    def __init__(self, channel: str) -> None:
        self._channel = channel

    def _refuse(self) -> DeliveryNotConfiguredError:
        return DeliveryNotConfiguredError(
            f"No {self._channel} delivery is configured for portal invitations."
        )

    def check_ready(self) -> None:
        raise self._refuse()

    # ``PortalInviteDelivery`` half.
    def send_invite(self, *, to_email: str, link: str) -> None:
        raise self._refuse()

    # ``SmsGateway`` half.
    def send(self, *, to: str, body: str) -> None:
        raise self._refuse()
