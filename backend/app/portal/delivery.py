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

**Who an email is from is the practice's.** An email channel that can put the
practice's name, address and reply-to on a message implements
:class:`PracticeSenderDelivery`; the callers bind it to the practice with
:func:`sending_as_practice` just before they send, and
:mod:`app.portal.client_sender` decides what the practice's sender is. A
channel that does not implement it sends under the deployment's own name.

Unlike the invitation channels, an unconfigured notice channel is not a
refusal. :meth:`PortalNoticeDelivery.can_deliver` is what a caller asks, and
a deployment that has wired nothing simply sends nothing: asking a patient
to correct an answer is a thing the practice did, and it has to be recorded
whether or not there is a mail server to mention it to.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol, Self, cast, runtime_checkable

if TYPE_CHECKING:
    from collections.abc import Callable


class DeliveryNotConfiguredError(RuntimeError):
    """No adapter is wired for this channel, so nothing was sent."""


@dataclass(frozen=True)
class ClientSender:
    """Who an email to a client says it is from, and where a reply goes.

    Resolved per practice by :func:`app.portal.client_sender.resolve_client_sender`.

    ``from_address`` is set only when the practice's own domain can send;
    ``None`` means the deployment's own address, still under ``from_name``.
    ``reply_to`` is ``None`` until the practice saves one; a channel then adds
    no Reply-To, and ``from_address`` is ``None`` too.
    """

    from_name: str
    from_address: str | None
    reply_to: str | None


@runtime_checkable
class PracticeSenderDelivery(Protocol):
    """An email channel that can send as the practice.

    Optional, like :class:`RenderedInviteDelivery`: a channel that does not
    implement it keeps sending under the deployment's own name, and the
    practice's sender settings say they do not apply here. One that does
    returns a copy of itself that puts *sender* on every message it sends.
    """

    def sending_as(self, sender: ClientSender) -> Self:
        """This channel, sending as *sender*."""
        ...


def sending_as_practice[D](delivery: D, sender: Callable[[], ClientSender | None]) -> D:
    """*delivery* sending as the practice where it can, else *delivery* unchanged.

    *sender* is asked only when the channel can use the answer, so a channel
    that sends under the deployment's name costs no lookup. A ``None`` answer
    (no practice to send as) also leaves *delivery* unchanged.
    """
    if isinstance(delivery, PracticeSenderDelivery):
        resolved = sender()
        if resolved is not None:
            return cast("D", delivery.sending_as(resolved))
    return delivery


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


@runtime_checkable
class RenderedInviteDelivery(Protocol):
    """An invite channel that can send wording the practice wrote.

    Optional, and deliberately a separate port. An adapter that implements
    it is saying its provider may carry whatever a practice typed — which is
    a statement about that provider's agreement to handle it, not about
    code. An adapter that does not keeps sending its own fixed wording, and
    the practice is not offered an editor whose text would never be used.

    ``text`` is plain text rendered by :mod:`app.portal.invite_email`, and
    carries the magic link: it is a credential, and is never logged.
    """

    def send_rendered_invite(self, *, to_email: str, subject: str, text: str) -> None:
        """Email one rendered invitation. Raises on delivery failure."""


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
PORTAL_NOTICES: frozenset[str] = frozenset(
    {"intake_correction_requested", "refill_request_decided"}
)


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
    #: Set when the invitation went through the rendered path.
    subject: str | None = None
    text: str | None = None


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


class CapturingRenderedInviteDelivery(CapturingInviteDelivery):
    """Records invitations sent as practice-written text. For tests.

    ``link`` on each record is read back out of the text, so a test can
    redeem it the same way as one from the fixed-wording double.
    """

    def send_rendered_invite(self, *, to_email: str, subject: str, text: str) -> None:
        link = next((word for word in text.split() if word.startswith("http")), "")
        self.sent.append(SentInviteEmail(to_email=to_email, link=link, subject=subject, text=text))


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
