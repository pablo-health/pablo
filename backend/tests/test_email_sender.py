# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Unit tests for the pluggable email-delivery backends.

The SMTP backend is exercised against a fake smtplib.SMTP client (a test
seam, mirroring the client_factory pattern in test_file_storage.py) — no
network, no real socket.
"""

from __future__ import annotations

import logging
from typing import Any

import pytest
from app.services.email_sender import (
    EmailSender,
    InMemoryEmailSender,
    NoneEmailSender,
    OutboundEmail,
    SmtpEmailSender,
    email_sender_from_settings,
)
from app.settings import Settings

_MESSAGE = OutboundEmail(
    to="patient@example.com",
    subject="Your appointment is confirmed",
    text="Your session is booked for 3pm. Manage it at https://example.com/manage/abc123",
    kind="booking_confirmation",
)


class _FakeSmtp:
    """Records call order; raises on the named step if configured to fail."""

    def __init__(self, *, fail_on: str | None = None) -> None:
        self.calls: list[str] = []
        self._fail_on = fail_on

    def starttls(self) -> None:
        self.calls.append("starttls")
        if self._fail_on == "starttls":
            raise ConnectionError("starttls failed")

    def login(self, username: str, password: str) -> None:
        self.calls.append("login")
        if self._fail_on == "login":
            raise ConnectionError("login failed")

    def send_message(self, message: Any) -> None:
        self.calls.append("send_message")
        if self._fail_on == "send_message":
            raise ConnectionError("send failed")

    def quit(self) -> None:
        self.calls.append("quit")


def _smtp_sender(*, fake: _FakeSmtp) -> SmtpEmailSender:
    return SmtpEmailSender(
        host="smtp.example.com",
        port=587,
        username="user",
        password="secret",  # noqa: S106 — dummy test credential
        from_addr="notifications@example.com",
        client_factory=lambda: fake,
    )


def _no_phi(caplog: pytest.LogCaptureFixture) -> None:
    haystacks = [record.getMessage() for record in caplog.records]
    for needle in (_MESSAGE.to, _MESSAGE.subject, _MESSAGE.text):
        assert all(needle not in text for text in haystacks)


class TestNoneEmailSender:
    def test_send_returns_normally(self) -> None:
        NoneEmailSender().send(_MESSAGE)

    def test_logs_kind_but_no_phi(self, caplog: pytest.LogCaptureFixture) -> None:
        with caplog.at_level(logging.INFO):
            NoneEmailSender().send(_MESSAGE)
        assert any(_MESSAGE.kind in record.getMessage() for record in caplog.records)
        _no_phi(caplog)

    def test_cannot_deliver(self) -> None:
        assert NoneEmailSender().can_deliver is False


class TestSmtpEmailSender:
    def test_starttls_before_login(self) -> None:
        fake = _FakeSmtp()
        _smtp_sender(fake=fake).send(_MESSAGE)
        assert fake.calls.index("starttls") < fake.calls.index("login")
        assert fake.calls == ["starttls", "login", "send_message", "quit"]

    def test_no_phi_on_success(self, caplog: pytest.LogCaptureFixture) -> None:
        with caplog.at_level(logging.INFO):
            _smtp_sender(fake=_FakeSmtp()).send(_MESSAGE)
        _no_phi(caplog)

    def test_failure_propagates(self) -> None:
        fake = _FakeSmtp(fail_on="send_message")
        with pytest.raises(ConnectionError):
            _smtp_sender(fake=fake).send(_MESSAGE)
        # Connection is still torn down even on failure.
        assert fake.calls[-1] == "quit"

    def test_no_phi_on_failure(self, caplog: pytest.LogCaptureFixture) -> None:
        fake = _FakeSmtp(fail_on="send_message")
        with caplog.at_level(logging.INFO), pytest.raises(ConnectionError):
            _smtp_sender(fake=fake).send(_MESSAGE)
        _no_phi(caplog)

    def test_can_deliver(self) -> None:
        assert _smtp_sender(fake=_FakeSmtp()).can_deliver is True


class TestInMemoryEmailSender:
    def test_records_sent_messages_in_order(self) -> None:
        sender = InMemoryEmailSender()
        second = OutboundEmail(to="b@example.com", subject="s2", text="t2", kind="reminder")
        sender.send(_MESSAGE)
        sender.send(second)
        assert sender.sent == [_MESSAGE, second]

    def test_can_deliver(self) -> None:
        assert InMemoryEmailSender().can_deliver is True


def _settings(**overrides: Any) -> Settings:
    return Settings(
        database_url="postgresql://test:test@localhost:5432/test",
        **overrides,
    )


class TestEmailSenderFromSettings:
    def test_defaults_to_none(self) -> None:
        sender: EmailSender = email_sender_from_settings(_settings())
        assert isinstance(sender, NoneEmailSender)

    def test_smtp_backend_plumbs_settings(self) -> None:
        sender = email_sender_from_settings(
            _settings(
                email_backend="smtp",
                smtp_host="smtp.example.com",
                smtp_port=2525,
                smtp_username="user",
                smtp_password="secret",  # noqa: S106 — dummy test credential
                smtp_from="notifications@example.com",
            )
        )
        assert isinstance(sender, SmtpEmailSender)
        assert sender.can_deliver is True
