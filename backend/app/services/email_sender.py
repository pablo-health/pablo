# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Pluggable email delivery for best-effort notifications (e.g. booking
confirmations, reminders).

One interface, two backends plus a test double:

* :class:`NoneEmailSender` — the default. Logs that a send was suppressed
  and returns; no message ever leaves the process. This is what a bare
  deployment gets with no configuration.
* :class:`SmtpEmailSender` — ``smtplib`` with STARTTLS. Every provider
  speaks SMTP, so this is the one backend the engine hard-codes; further
  adapters go behind :class:`EmailSender` later without touching callers.
* :class:`InMemoryEmailSender` — captures sent messages for tests, mirroring
  the in-memory repositories used elsewhere in this codebase.

Selection is a configuration change (``EMAIL_BACKEND=none|smtp``), not a
code change — see :func:`email_sender_from_settings`. Callers hold an
:class:`EmailSender` and never touch ``smtplib`` directly.

Delivery is not guaranteed. ``NoneEmailSender`` silently drops every
message, which is fine for a passive notification but a hazard for
anything a user is waiting on (a booking confirmation that never arrives,
with nothing anywhere saying why). :attr:`EmailSender.can_deliver` is the
seam for that: ``False`` on ``NoneEmailSender``, ``True`` on the other two.
A caller that requires confirmed delivery checks it and refuses to arm
rather than accepting a booking it can never confirm.

``send`` raises on failure — it never swallows. Turning a failed
notification into a best-effort side effect (log and move on, don't fail
the surrounding operation) is the caller's job, not this module's.

PHI note: a recipient address on an appointment implies a care
relationship, so it is PHI-adjacent. No backend here logs the address,
subject, or body — only ``kind``, and only success/failure for SMTP.
"""

from __future__ import annotations

import logging
import smtplib
import ssl
from dataclasses import dataclass, field
from email.message import EmailMessage
from typing import TYPE_CHECKING, Any, Protocol

from ..settings import Settings, get_settings

if TYPE_CHECKING:
    from collections.abc import Callable

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class OutboundEmail:
    """A plain-text email to send. HTML is deferred — text only."""

    to: str
    subject: str
    text: str
    kind: str


class EmailSender(Protocol):
    """Email delivery needed by notification surfaces.

    ``kind`` on the message is for logging and test assertions only — it
    never selects a provider; the backend is chosen once, at
    :func:`email_sender_from_settings`.
    """

    can_deliver: bool

    def send(self, message: OutboundEmail) -> None:
        """Send ``message``, raising on failure.

        Never swallows an error — a caller that treats delivery as a
        best-effort side effect (must not fail the operation it's attached
        to) is responsible for catching and logging. No retry, no queue,
        no backoff here.
        """
        ...


class NoneEmailSender:
    """Default backend: logs that a send was suppressed and returns.

    Cannot deliver — ``can_deliver`` is ``False`` so a caller that requires
    confirmed delivery can check it and refuse rather than silently no-op.
    """

    can_deliver = False

    def send(self, message: OutboundEmail) -> None:
        logger.info("email suppressed (no backend configured): kind=%s", message.kind)


class SmtpEmailSender:
    """SMTP backend with STARTTLS.

    The connection is bounded by a fifteen-second timeout so a stalled
    provider can't hang the caller indefinitely, and STARTTLS negotiates
    with the platform's default certificate store — the same verification
    a browser applies — rather than accepting whatever certificate the
    server happens to present.

    ``client_factory`` is a test seam, mirroring the pattern in
    ``file_storage.py`` — production connects a real ``smtplib.SMTP``
    lazily so importing this module never opens a socket.
    """

    can_deliver = True

    def __init__(
        self,
        *,
        host: str,
        port: int,
        username: str,
        password: str,
        from_addr: str,
        client_factory: Callable[[int], Any] | None = None,
    ) -> None:
        self._host = host
        self._port = port
        self._username = username
        self._password = password
        self._from_addr = from_addr
        self._client_factory = client_factory

    def _client(self) -> Any:
        if self._client_factory is not None:
            return self._client_factory(15)
        return smtplib.SMTP(self._host, self._port, timeout=15)

    def send(self, message: OutboundEmail) -> None:
        email_message = EmailMessage()
        email_message["Subject"] = message.subject
        email_message["From"] = self._from_addr
        email_message["To"] = message.to
        email_message.set_content(message.text)

        server = self._client()
        try:
            server.starttls(context=ssl.create_default_context())
            server.login(self._username, self._password)
            server.send_message(email_message)
        except Exception:
            logger.warning("smtp send failed: kind=%s", message.kind)
            raise
        else:
            logger.info("smtp send succeeded: kind=%s", message.kind)
        finally:
            server.quit()


@dataclass
class InMemoryEmailSender:
    """Test double that records every sent message in send order."""

    can_deliver = True
    sent: list[OutboundEmail] = field(default_factory=list)

    def send(self, message: OutboundEmail) -> None:
        self.sent.append(message)


def email_sender_from_settings(settings: Settings) -> EmailSender:
    """Construct the configured backend. ``none`` (log-only) is the default."""
    if settings.email_backend == "smtp":
        return SmtpEmailSender(
            host=settings.smtp_host,
            port=settings.smtp_port,
            username=settings.smtp_username,
            password=settings.smtp_password.get_secret_value(),
            from_addr=settings.smtp_from,
        )
    return NoneEmailSender()


def get_email_sender() -> EmailSender:
    """FastAPI dependency — the configured backend for this deployment."""
    return email_sender_from_settings(get_settings())
