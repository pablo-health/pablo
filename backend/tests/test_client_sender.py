# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Who client email is from: the settings' validation, the routes, and the SMTP wire.

The resolution rule itself (verified domain or not, which domain, the
defaults, a person's own mailbox) reads platform tables and is proven against
Postgres in ``tests_integration/database/test_client_sender_db.py``. Here the
store is a fake and the owner check goes through the real function with the
practice row patched in, as ``test_practice_domains_routes.py`` does.
"""

from __future__ import annotations

import json
from email import message_from_bytes, policy
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any
from unittest.mock import MagicMock, patch

import pytest
from app.api_errors import register_exception_handlers
from app.auth.service import require_active_subscription
from app.models.audit import AuditAction, ResourceType
from app.portal import client_sender, notices, sender_routes
from app.portal.adapters import SmtpInviteDelivery
from app.portal.client_sender import (
    SenderDefaults,
    SenderSettings,
    SenderSettingsError,
    SenderView,
    clean_local_part,
    clean_reply_to,
    clean_sender_name,
    get_sender_settings_store,
)
from app.portal.delivery import (
    CapturingInviteDelivery,
    ClientSender,
    PracticeSenderDelivery,
    sending_as_practice,
)
from app.portal.factory import get_invite_delivery
from app.portal.practice_routes import PracticeAddress
from app.services.audit_service import get_audit_service
from app.services.email_sender import InMemoryEmailSender, OutboundEmail, SmtpEmailSender
from fastapi import FastAPI
from fastapi.testclient import TestClient

from tests.client_sender_doubles import EXAMPLE_SENDER, SenderAwareNoticeDelivery

if TYPE_CHECKING:
    from collections.abc import Iterator

    from app.models import User

PRACTICE_ID = "practice-1"
URL = "/api/practice/email-sender"
SENDER = EXAMPLE_SENDER


# ── validation ────────────────────────────────────────────────────────────


@pytest.mark.parametrize("raw", [None, "", "   "])
def test_a_blank_field_means_the_default(raw: str | None) -> None:
    assert clean_sender_name(raw) is None
    assert clean_local_part(raw) is None
    assert clean_reply_to(raw) is None


def test_a_sender_name_is_trimmed_and_may_carry_a_credential() -> None:
    assert clean_sender_name("  Jordan Rivera, LCSW ") == "Jordan Rivera, LCSW"


@pytest.mark.parametrize("raw", ["Jordan\r\nBcc: x@example.com", "Tab\there", "x" * 101])
def test_a_sender_name_that_could_not_be_a_header_is_refused(raw: str) -> None:
    with pytest.raises(SenderSettingsError):
        clean_sender_name(raw)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("portal", "portal"),
        (" Hello ", "hello"),
        ("front.desk", "front.desk"),
        ("a+b_c-d", "a+b_c-d"),
    ],
)
def test_a_mailbox_name_is_lowercased(raw: str, expected: str) -> None:
    assert clean_local_part(raw) == expected


@pytest.mark.parametrize(
    "raw", ["portal@example.com", "two words", ".portal", "portal.", "a..b", "ümlaut", "x" * 65]
)
def test_a_mailbox_name_that_is_not_one_is_refused(raw: str) -> None:
    with pytest.raises(SenderSettingsError):
        clean_local_part(raw)


@pytest.mark.parametrize(
    "raw", ["noreply", "no-reply", "No_Reply", "donotreply", "do-not-reply", "do.not.reply"]
)
def test_a_no_reply_mailbox_is_refused_with_a_suggestion(raw: str) -> None:
    with pytest.raises(SenderSettingsError, match="portal or hello"):
        clean_local_part(raw)


def test_a_reply_to_address_is_validated() -> None:
    assert clean_reply_to(" FrontDesk@Example.com ") == "FrontDesk@example.com"
    with pytest.raises(SenderSettingsError):
        clean_reply_to("not an address")


# ── the SMTP wire ─────────────────────────────────────────────────────────


class _Smtp:
    """Stands in for ``smtplib.SMTP``; keeps the bytes it was handed."""

    def __init__(self) -> None:
        self.sent: list[Any] = []

    def starttls(self, **_kwargs: Any) -> None: ...

    def login(self, *_args: Any) -> None: ...

    def send_message(self, message: Any) -> None:
        self.sent.append(message_from_bytes(bytes(message), policy=policy.default))

    def quit(self) -> None: ...


def _smtp_sender(server: _Smtp) -> SmtpEmailSender:
    return SmtpEmailSender(
        host="smtp.example.com",
        port=587,
        username="u",
        password="secret",  # noqa: S106 — dummy test credential
        from_addr="Example Deployment <mail@deploy.example.com>",
        client_factory=lambda _timeout: server,
    )


def _message(**sender: str | None) -> OutboundEmail:
    return OutboundEmail(
        to="client@example.com", subject="Hello", text="Body", kind="test", **sender
    )


def test_smtp_keeps_the_configured_from_when_nothing_overrides_it() -> None:
    server = _Smtp()
    _smtp_sender(server).send(_message())
    sent = server.sent[0]
    assert sent["From"] == "Example Deployment <mail@deploy.example.com>"
    assert sent["Reply-To"] is None


def test_smtp_sends_from_the_practice_domain_with_its_name_and_reply_to() -> None:
    server = _Smtp()
    _smtp_sender(server).send(
        _message(
            from_name="Jordan Rivera, LCSW",
            from_address="portal@example.com",
            reply_to="frontdesk@example.com",
        )
    )
    sent = server.sent[0]
    assert sent["From"].addresses[0].display_name == "Jordan Rivera, LCSW"
    assert sent["From"].addresses[0].addr_spec == "portal@example.com"
    # The comma is quoted, so the name stays one address rather than two.
    assert len(sent["From"].addresses) == 1
    assert sent["Reply-To"] == "frontdesk@example.com"


def test_smtp_keeps_its_own_address_under_the_practice_name_while_the_domain_cannot_send() -> None:
    server = _Smtp()
    _smtp_sender(server).send(
        _message(from_name="Jordan Rivera, LCSW", reply_to="frontdesk@example.com")
    )
    address = server.sent[0]["From"].addresses[0]
    assert address.display_name == "Jordan Rivera, LCSW"
    assert address.addr_spec == "mail@deploy.example.com"


# ── the channel ───────────────────────────────────────────────────────────


def test_the_smtp_invite_channel_sends_as_the_practice_once_bound() -> None:
    sender = InMemoryEmailSender()
    delivery = SmtpInviteDelivery(sender=sender)
    assert isinstance(delivery, PracticeSenderDelivery)

    delivery.sending_as(SENDER).send_rendered_invite(
        to_email="client@example.com", subject="s", text="t"
    )
    delivery.send_rendered_invite(to_email="client@example.com", subject="s", text="t")

    bound, unbound = sender.sent
    assert (bound.from_name, bound.from_address, bound.reply_to) == (
        "Jordan Rivera, LCSW",
        "portal@example.com",
        "frontdesk@example.com",
    )
    # Binding returns a copy; the channel it came from is unchanged.
    assert (unbound.from_name, unbound.from_address, unbound.reply_to) == (None, None, None)


def test_a_channel_that_cannot_send_as_the_practice_is_not_asked_who_it_is() -> None:
    delivery = CapturingInviteDelivery()

    def never() -> ClientSender:
        raise AssertionError("resolved for a channel that cannot use it")

    assert sending_as_practice(delivery, never) is delivery


def test_no_practice_to_send_as_leaves_the_channel_as_it_was() -> None:
    delivery = SmtpInviteDelivery(sender=InMemoryEmailSender())
    assert sending_as_practice(delivery, lambda: None) is delivery


def test_a_portal_notice_goes_out_as_the_clinicians_practice(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        notices, "_resolve_practice_from_email", lambda _email: (PRACTICE_ID, "schema")
    )
    monkeypatch.setattr(
        notices,
        "ensure_practice_slug",
        lambda _pid: PracticeAddress(slug="example", display_name="Example", enabled=True),
    )
    monkeypatch.setattr(
        notices, "build_portal_link", lambda *, slug: f"https://p.example.com/{slug}"
    )
    asked: list[str] = []

    def resolve(practice_id: str) -> ClientSender:
        asked.append(practice_id)
        return EXAMPLE_SENDER

    monkeypatch.setattr(notices, "resolve_client_sender", resolve)
    delivery = SenderAwareNoticeDelivery()

    sent = notices.send_portal_notice(
        delivery,
        notice="refill_request_decided",
        to_email="client@example.com",
        from_clinician_email="clinician@example.com",
    )

    assert sent is True
    assert asked == [PRACTICE_ID]
    assert delivery.sent_as == [EXAMPLE_SENDER]


def test_a_notice_whose_sender_cannot_be_found_is_not_sent_and_not_raised(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Best-effort, like any other notice failure: the practice's act stands."""
    monkeypatch.setattr(
        notices, "_resolve_practice_from_email", lambda _email: (PRACTICE_ID, "schema")
    )
    monkeypatch.setattr(
        notices,
        "ensure_practice_slug",
        lambda _pid: PracticeAddress(slug="example", display_name="Example", enabled=True),
    )
    monkeypatch.setattr(
        notices, "build_portal_link", lambda *, slug: f"https://p.example.com/{slug}"
    )

    def broken(_practice_id: str) -> ClientSender:
        raise LookupError("no such practice")

    monkeypatch.setattr(notices, "resolve_client_sender", broken)
    delivery = SenderAwareNoticeDelivery()

    assert (
        notices.send_portal_notice(
            delivery,
            notice="refill_request_decided",
            to_email="client@example.com",
            from_clinician_email="clinician@example.com",
        )
        is False
    )
    assert delivery.sent == []


# ── the routes ────────────────────────────────────────────────────────────


class _FakeStore:
    def __init__(self) -> None:
        self.chosen = SenderSettings()
        self.saved_by: str | None = None

    def view(self, practice_id: str) -> SenderView:
        assert practice_id == PRACTICE_ID
        return SenderView(
            chosen=self.chosen,
            defaults=SenderDefaults(
                sender_name="Example Therapy",
                sender_local_part="portal",
            ),
            sending_domain=None,
            effective=ClientSender(
                from_name=self.chosen.sender_name or "Example Therapy",
                from_address=None,
                reply_to=self.chosen.reply_to,
            ),
        )

    def save(self, practice_id: str, chosen: SenderSettings, *, by: str) -> SenderView:
        if chosen.sender_local_part == "taken":
            raise SenderSettingsError("taken@example.com is someone's own address.")
        self.chosen = chosen
        self.saved_by = by
        return self.view(practice_id)


class _RecordingAudit:
    def __init__(self) -> None:
        self.entries: list[dict[str, Any]] = []

    def log(self, action: Any, *_args: Any, **kwargs: Any) -> None:
        self.entries.append({"action": action, **kwargs})


@pytest.fixture
def store() -> _FakeStore:
    return _FakeStore()


@pytest.fixture
def audit() -> _RecordingAudit:
    return _RecordingAudit()


@pytest.fixture
def owner_email() -> str:
    """The practice's registered owner; the test user is test@example.com."""
    return "test@example.com"


@pytest.fixture
def channel() -> Any:
    return SmtpInviteDelivery(sender=InMemoryEmailSender())


@pytest.fixture
def client(
    mock_user: User, store: _FakeStore, audit: _RecordingAudit, owner_email: str, channel: Any
) -> Iterator[TestClient]:
    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(sender_routes.router)
    app.dependency_overrides[require_active_subscription] = lambda: mock_user
    app.dependency_overrides[get_sender_settings_store] = lambda: store
    app.dependency_overrides[get_audit_service] = lambda: audit
    app.dependency_overrides[get_invite_delivery] = lambda: channel

    session = MagicMock()
    session.get.return_value = SimpleNamespace(id=PRACTICE_ID, owner_email=owner_email)
    with (
        patch(
            "app.auth.service._resolve_practice_from_email",
            return_value=(PRACTICE_ID, "practice_1"),
        ),
        patch("app.db.get_db_session", return_value=session),
    ):
        yield TestClient(app)


def test_the_owner_reads_the_defaults(client: TestClient) -> None:
    body = client.get(URL).json()
    assert body["can_edit"] is True
    assert body["applies"] is True
    assert body["chosen"] == {"sender_name": None, "sender_local_part": None, "reply_to": None}
    # The reply-to has no default, and the owner's address appears nowhere.
    assert body["defaults"] == {"sender_name": "Example Therapy", "sender_local_part": "portal"}
    assert body["effective"] == {
        "from_name": "Example Therapy",
        "from_address": None,
        "reply_to": None,
    }
    assert "test@example.com" not in json.dumps(body)


def test_with_no_reply_to_the_smtp_message_carries_no_reply_to_header() -> None:
    """The deployment's ordinary reply behaviour: no Reply-To, deployment From."""
    server = _Smtp()
    delivery = SmtpInviteDelivery(sender=_smtp_sender(server))

    delivery.sending_as(
        ClientSender(from_name="Example Therapy", from_address=None, reply_to=None)
    ).send_rendered_invite(to_email="client@example.com", subject="s", text="t")

    sent = server.sent[0]
    assert sent["Reply-To"] is None
    assert sent["From"].addresses[0].display_name == "Example Therapy"
    assert sent["From"].addresses[0].addr_spec == "mail@deploy.example.com"


def test_the_owner_saves_the_three_fields_and_the_change_is_audited(
    client: TestClient, store: _FakeStore, audit: _RecordingAudit, mock_user: User
) -> None:
    response = client.put(
        URL,
        json={
            "sender_name": " Jordan Rivera, LCSW ",
            "sender_local_part": "Hello",
            "reply_to": "frontdesk@example.com",
        },
    )

    assert response.status_code == 200, response.text
    assert response.json()["chosen"] == {
        "sender_name": "Jordan Rivera, LCSW",
        "sender_local_part": "hello",
        "reply_to": "frontdesk@example.com",
    }
    assert store.saved_by == mock_user.id
    (entry,) = audit.entries
    assert entry["action"] == AuditAction.PRACTICE_EMAIL_SENDER_CHANGED
    assert entry["resource_type"] == ResourceType.PRACTICE
    assert entry["resource_id"] == PRACTICE_ID


def test_blank_fields_go_back_to_their_defaults(client: TestClient, store: _FakeStore) -> None:
    store.chosen = SenderSettings(
        sender_name="Old", sender_local_part="old", reply_to="a@example.com"
    )
    response = client.put(URL, json={"sender_name": "", "sender_local_part": " ", "reply_to": None})
    assert response.status_code == 200
    assert store.chosen == SenderSettings()


@pytest.mark.parametrize("owner_email", ["someone-else@example.com"])
def test_a_clinician_who_does_not_own_the_practice_reads_but_cannot_save(
    client: TestClient, store: _FakeStore, audit: _RecordingAudit
) -> None:
    assert client.get(URL).json()["can_edit"] is False

    response = client.put(URL, json={"sender_name": "Mine now"})

    assert response.status_code == 403
    assert store.chosen == SenderSettings()
    assert audit.entries == []


@pytest.mark.parametrize(
    "body",
    [
        {"sender_local_part": "noreply"},
        {"sender_local_part": "a@b"},
        {"reply_to": "nope"},
        {"sender_name": "Line\nbreak"},
        {"sender_local_part": "taken"},
    ],
)
def test_a_setting_that_cannot_be_saved_is_a_422_that_says_why(
    client: TestClient, store: _FakeStore, audit: _RecordingAudit, body: dict[str, str]
) -> None:
    response = client.put(URL, json=body)

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "EMAIL_SENDER_INVALID"
    assert response.json()["error"]["message"]
    assert store.chosen == SenderSettings()
    assert audit.entries == []


@pytest.mark.parametrize("channel", [CapturingInviteDelivery()])
def test_a_channel_that_cannot_send_as_the_practice_says_the_settings_do_not_apply(
    client: TestClient,
) -> None:
    assert client.get(URL).json()["applies"] is False


# ── the deployment's own address, for the preview ─────────────────────────


@pytest.fixture
def _no_registration() -> Iterator[None]:
    client_sender.reset_deployment_from_address()
    yield
    client_sender.reset_deployment_from_address()


@pytest.mark.usefixtures("_no_registration")
def test_the_deployment_address_comes_from_smtp_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        client_sender,
        "get_settings",
        lambda: SimpleNamespace(email_backend="smtp", smtp_from="Deploy <mail@deploy.example.com>"),
    )
    assert client_sender.deployment_from_address() == "mail@deploy.example.com"


@pytest.mark.usefixtures("_no_registration")
def test_a_registered_deployment_address_wins() -> None:
    client_sender.register_deployment_from_address(lambda: "notify@deploy.example.com")
    assert client_sender.deployment_from_address() == "notify@deploy.example.com"
