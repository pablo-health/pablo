# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Practice invitations: who may send them, and the no-practice sign-in that redeems them.

* only the owner or an admin may create, list or withdraw an invitation; any
  other clinician gets 403 and nothing is written, audited or sent;
* the owner's invitation is emailed with its link, and the response never
  carries the token; only the token's hash is stored;
* ``get_verified_invitee`` is used by the redeem route and by no other route,
  and refuses a sign-in whose address the issuer has not verified;
* a membership hook that raises is logged and never stops the others.

The routes run with the request session stood in; what they write is proven
against Postgres in ``tests_integration/api/test_practice_invites.py``.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any
from unittest.mock import MagicMock, patch

import pytest
from app.api_errors import register_exception_handlers
from app.auth.invite_identity import get_verified_invitee
from app.auth.providers import VerifiedIdentity
from app.auth.service import get_current_user, require_active_subscription
from app.db import practice_members
from app.db.platform_models import PracticeInviteRow
from app.main import app as real_app
from app.models import User
from app.models.audit import AuditAction
from app.route_introspection import iter_api_routes
from app.routes import practice_invites as invite_routes
from app.services import practice_invites as invites
from app.services.audit_service import get_audit_service
from app.services.email_sender import InMemoryEmailSender
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

if TYPE_CHECKING:
    from collections.abc import Iterator

    from app.repositories.practice_admin import InMemoryPracticeAdminRepository

    from tests.audit_fakes import RecordingAudit

URL = "/api/practice/invites"
OWNER_ID = "11111111-1111-1111-1111-111111111111"
COLLEAGUE_ID = "22222222-2222-2222-2222-222222222222"


def _user(user_id: str, email: str, name: str) -> User:
    return User(
        id=user_id,
        email=email,
        name=name,
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
        baa_accepted_at=datetime(2026, 1, 1, tzinfo=UTC),
        baa_version="2024-01-01",
    )


OWNER = _user(OWNER_ID, "owner@example.com", "Olive Owner")
COLLEAGUE = _user(COLLEAGUE_ID, "colleague@example.com", "Casey Colleague")


@pytest.fixture
def acting() -> dict[str, User]:
    return {"user": OWNER}


@pytest.fixture
def sender() -> InMemoryEmailSender:
    return InMemoryEmailSender()


@pytest.fixture
def session() -> MagicMock:
    db = MagicMock()
    db.get.return_value = SimpleNamespace(
        id="practice-1",
        name="Harbor Counseling",
        owner_email="owner@example.com",
        schema_name="practice_1",
    )
    return db


@pytest.fixture
def client(
    acting: dict[str, User],
    recording_audit: RecordingAudit,
    practice_admins: InMemoryPracticeAdminRepository,
    sender: InMemoryEmailSender,
    session: MagicMock,
) -> Iterator[TestClient]:
    del practice_admins  # nobody is an admin unless a test grants it
    application = FastAPI()
    register_exception_handlers(application)
    application.include_router(invite_routes.router)
    application.dependency_overrides[get_current_user] = lambda: acting["user"]
    application.dependency_overrides[require_active_subscription] = lambda: acting["user"]
    application.dependency_overrides[get_audit_service] = lambda: recording_audit
    application.dependency_overrides[invite_routes.get_invite_email_sender] = lambda: sender
    with (
        patch("app.auth.service._resolve_practice", return_value=("practice-1", "practice_1")),
        patch("app.db.get_db_session", return_value=session),
        patch.object(invite_routes, "get_db_session", return_value=session),
    ):
        yield TestClient(application)


def _code(response: Any) -> str:
    return str(response.json()["error"]["code"])


def _added_invites(session: MagicMock) -> list[PracticeInviteRow]:
    return [
        call.args[0]
        for call in session.add.call_args_list
        if isinstance(call.args[0], PracticeInviteRow)
    ]


# --- who may send an invitation ---------------------------------------------


def test_a_clinician_who_is_neither_owner_nor_admin_gets_403_everywhere(
    client: TestClient,
    acting: dict[str, User],
    recording_audit: RecordingAudit,
    sender: InMemoryEmailSender,
    session: MagicMock,
) -> None:
    acting["user"] = COLLEAGUE
    responses = {
        "create": client.post(URL, json={"email": "new@example.com"}),
        "list": client.get(URL),
        "revoke": client.delete(f"{URL}/33333333-3333-3333-3333-333333333333"),
    }
    for action, response in responses.items():
        assert response.status_code == 403, action
        assert _code(response) == "NOT_PRACTICE_ADMIN", action
    assert _added_invites(session) == []
    session.commit.assert_not_called()
    session.execute.assert_not_called()
    assert recording_audit.entries == []
    assert sender.sent == []


def test_an_admin_may_invite(
    client: TestClient,
    acting: dict[str, User],
    practice_admins: InMemoryPracticeAdminRepository,
    session: MagicMock,
) -> None:
    practice_admins.grant(COLLEAGUE_ID, granted_by=OWNER_ID, granted_at=datetime.now(UTC))
    acting["user"] = COLLEAGUE
    response = client.post(URL, json={"email": "new@example.com"})
    assert response.status_code == 201
    assert [invite.invited_by for invite in _added_invites(session)] == [COLLEAGUE_ID]


# --- what an invitation carries ---------------------------------------------


def test_the_owner_invites_by_email_and_the_response_never_carries_the_token(
    client: TestClient,
    recording_audit: RecordingAudit,
    sender: InMemoryEmailSender,
    session: MagicMock,
) -> None:
    response = client.post(URL, json={"email": "  New.Person@Example.com "})
    assert response.status_code == 201

    [stored] = _added_invites(session)
    assert stored.email == "New.Person@Example.com", "kept as typed, trimmed"
    assert stored.practice_id == "practice-1"
    assert stored.expires_at - stored.created_at == invites.INVITE_TTL
    session.commit.assert_called_once()

    [email] = sender.sent
    assert email.to == "New.Person@Example.com"
    assert email.kind == invites.INVITE_EMAIL_KIND
    assert "Harbor Counseling" in email.text
    assert "Olive Owner" in email.text
    token = email.text.split("/invite/", 1)[1].split()[0]
    assert invites.hash_token(token) == stored.token_hash, "only the hash is stored"
    assert token not in response.text, "the response never carries the token"
    assert token not in stored.token_hash

    body = response.json()
    assert body["id"] == stored.id
    assert body["email_sent"] is True
    assert set(body) == {"id", "email", "invited_by", "created_at", "expires_at", "email_sent"}
    assert [(e["action"], e["changes"]) for e in recording_audit.entries] == [
        (AuditAction.PRACTICE_INVITE_CREATED, {"invite_id": stored.id})
    ]


def test_a_deployment_with_no_mail_backend_says_the_email_did_not_go(
    client: TestClient, sender: InMemoryEmailSender, session: MagicMock
) -> None:
    sender.can_deliver = False  # type: ignore[misc]
    response = client.post(URL, json={"email": "new@example.com"})
    assert response.status_code == 201
    assert response.json()["email_sent"] is False
    assert sender.sent == []
    assert len(_added_invites(session)) == 1, "the invitation is still saved"


def test_an_email_that_fails_to_send_is_reported_not_raised(
    client: TestClient, sender: InMemoryEmailSender
) -> None:
    with patch.object(sender, "send", side_effect=OSError("smtp down")):
        response = client.post(URL, json={"email": "new@example.com"})
    assert response.status_code == 201
    assert response.json()["email_sent"] is False


@pytest.mark.parametrize("email", ["no-at-sign", "a@nodot", "two words@example.com", "@x.com"])
def test_something_that_is_not_an_address_is_refused(client: TestClient, email: str) -> None:
    assert client.post(URL, json={"email": email}).status_code == 422


# --- the no-practice identity -----------------------------------------------


def test_only_the_redeem_route_uses_the_no_practice_identity() -> None:
    """``get_verified_invitee`` resolves no practice and asks for no second
    factor, so it is fenced to the one route that needs it."""

    def uses(dependant: Any) -> bool:
        if dependant.call is get_verified_invitee:
            return True
        return any(uses(sub) for sub in dependant.dependencies)

    users = sorted(
        (method, path)
        for path, route in iter_api_routes(real_app)
        if uses(route.dependant)
        for method in route.methods or ()
        if method != "HEAD"
    )
    assert users == [("POST", "/api/invites/{token}/redeem")]


def _invitee(email: str, *, verified: bool, user_id: str | None = None) -> Any:
    identity = VerifiedIdentity(
        provider="firebase",
        subject_id="sub-123",
        email=email,
        email_verified=verified,
        mfa_satisfied=False,
        claims={"uid": "sub-123", "email": email, "email_verified": verified},
    )
    identity_repo = MagicMock()
    identity_repo.get_user_id.return_value = user_id
    request = SimpleNamespace(headers={}, state=SimpleNamespace())
    with (
        patch("app.auth.invite_identity._verify_request_identity", return_value=identity),
        patch("app.auth.idle_session.check_and_touch"),
    ):
        return get_verified_invitee(
            request,  # type: ignore[arg-type]
            SimpleNamespace(credentials="token"),  # type: ignore[arg-type]
            identity_repo,
        )


def test_the_invitee_identity_is_the_verified_address_and_any_existing_account() -> None:
    first = _invitee("New.Person@Example.com", verified=True)
    assert (first.email, first.user_id, first.subject_id) == (
        "new.person@example.com",
        None,
        "sub-123",
    )
    returning = _invitee("back@example.com", verified=True, user_id=OWNER_ID)
    assert returning.user_id == OWNER_ID


@pytest.mark.parametrize("email", ["unverified@example.com", ""])
def test_the_invitee_identity_refuses_an_address_the_issuer_has_not_verified(
    email: str,
) -> None:
    with pytest.raises(HTTPException) as refused:
        _invitee(email, verified=bool(not email))
    assert refused.value.status_code == 403
    assert refused.value.detail["error"]["code"] == "EMAIL_NOT_VERIFIED"  # type: ignore[index]


# --- the membership hook ----------------------------------------------------


def test_a_failing_membership_hook_is_logged_and_the_others_still_run(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setattr(practice_members, "_membership_hooks", [])
    seen: list[tuple[str, str, str]] = []

    def broken(_practice_id: str, _user_id: str, _change: str) -> None:
        raise RuntimeError("member count service down")

    practice_members.register_membership_hook(broken)
    practice_members.register_membership_hook(lambda p, u, c: seen.append((p, u, c)))
    with caplog.at_level(logging.WARNING, logger=practice_members.logger.name):
        practice_members.notify_membership_change("practice-1", OWNER_ID, "joined")

    assert seen == [("practice-1", OWNER_ID, "joined")]
    [record] = [r for r in caplog.records if r.name == practice_members.logger.name]
    assert "RuntimeError" in record.getMessage()
    assert "member count service down" not in record.getMessage()
