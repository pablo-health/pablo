# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""The channels the portal talks on, as ports.

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

**A notice is the third port, and it is deliberately the smallest.** Some
things a practice does leave something waiting in the portal — a form
reopened for corrections is the first. :class:`PortalNoticeDelivery` is how
a deployment says "tell them there is something there": a name from
:data:`PORTAL_NOTICES` and a link to the practice's own portal page. No
subject, no body, no substitutions a caller chooses. The reason is the
point rather than an omission — an email that said which form, or why, would
put a clinical fact in an inbox nobody proved anything about, and the whole
design of this portal is that the content lives behind two factors.

Unlike the invitation channels, an unconfigured notice channel is not a
refusal. :meth:`PortalNoticeDelivery.can_deliver` is what a caller asks, and
a deployment that has wired nothing simply sends nothing: asking a patient
to correct an answer is a thing the practice did, and it has to be recorded
whether or not there is a mail server to mention it to.
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


#: Every notice this engine knows how to ask for, by name.
#:
#: An allow-list rather than a free string, so the set of things a patient
#: can be emailed about is enumerable from one place and a caller cannot
#: invent a notice with a sentence of its own in the name.
PORTAL_NOTICES: frozenset[str] = frozenset({"intake_correction_requested"})


class PortalNoticeDelivery(Protocol):
    """Tells a patient there is something waiting in the portal."""

    def send_notice(self, *, to_email: str, notice: str, link: str) -> None:
        """Send one notice. Raises on delivery failure.

        ``notice`` is a name from :data:`PORTAL_NOTICES` and ``link`` points
        at the practice's own portal page. Nothing else: an adapter wording
        the message is the deployment's business, and it has nothing
        clinical to word it from.
        """
        ...

    def can_deliver(self) -> bool:
        """Whether a send would reach anybody.

        Asked before every send, because an unconfigured notice channel is
        a silence rather than a failure — see this module's docstring.
        """
        ...


@dataclass
class SentNotice:
    to_email: str
    notice: str
    link: str


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


class CapturingNoticeDelivery:
    """Records notices instead of sending them. For tests.

    ``sent`` is the ordered log. Unlike :class:`CapturingInviteDelivery`
    nothing here is a credential — a notice carries a link to a page that
    asks for two factors — but it is still a test double and belongs to
    test code.
    """

    def __init__(self) -> None:
        self.sent: list[SentNotice] = []

    def send_notice(self, *, to_email: str, notice: str, link: str) -> None:
        self.sent.append(SentNotice(to_email=to_email, notice=notice, link=link))

    def can_deliver(self) -> bool:
        return True


class NoticesNotConfigured:
    """The default: a deployment that mentions nothing to anybody.

    ``can_deliver`` is False, so a caller skips the send rather than
    handling a refusal. :meth:`send_notice` still raises, for the caller
    that asks anyway — a silent no-op there would make a broken caller look
    like a working one.
    """

    def can_deliver(self) -> bool:
        return False

    def send_notice(self, *, to_email: str, notice: str, link: str) -> None:
        raise DeliveryNotConfiguredError("No portal notice delivery is configured.")


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
