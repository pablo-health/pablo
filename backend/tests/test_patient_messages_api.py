# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""HTTP-level tests for secure patient messaging.

The patient is a real principal here, so the suite runs through the actual
``get_patient_context`` dependency with a two-patient resolver and the
session arming patched out (no database). The clinician half runs through
the ordinary user dependency with an explicit ``has_patient_access`` grant,
because "who may read this thread" is the question the table exists to
answer and a blanket grant would stop asking it.

Two isolation directions are asserted in both files that cover this table:
here, through the routes; and in the integration suite, through the row
policies underneath them. Neither is the reason to skip the other.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING
from unittest.mock import patch

import pytest
from app.auth import patient_context as patient_context_module
from app.auth.patient_context import (
    AuthStrength,
    PatientContext,
    PatientCredential,
    PatientResolverRegistry,
    get_patient_resolver_registry,
)
from app.main import app
from app.models import PatientMessage
from app.models.audit import ACTOR_TYPE_CLINICIAN, ACTOR_TYPE_PATIENT, AuditAction
from app.models.patient_message_api import MAX_MESSAGE_BODY
from app.rate_limit import reset_patient_message_send_limiter
from app.repositories import InMemoryPatientMessageRepository
from app.routes.patient_messages import get_patient_message_repository
from app.services.patient_message_hooks import get_patient_message_hook_registry
from fastapi import HTTPException, status

if TYPE_CHECKING:
    from app.services import AuditService
    from fastapi.testclient import TestClient

_PATIENT_A = "patient-a"
_PATIENT_B = "patient-b"
_TOKEN_A = "credential-of-patient-a"
_TOKEN_B = "credential-of-patient-b"

_SUBJECT = "question about my refill"
_BODY = "I have been feeling worse since Tuesday."

PATIENT_BASE = "/api/patient/messages/threads"


class _TwoPatientResolver:
    credential_kind = "bearer"

    def resolve(self, credential: PatientCredential) -> PatientContext | None:
        patients = {_TOKEN_A: _PATIENT_A, _TOKEN_B: _PATIENT_B}
        patient_id = patients.get(credential.value)
        if patient_id is None:
            return None
        return PatientContext(
            patient_id=patient_id,
            practice_schema="practice_test",
            credential_kind="bearer",
            auth_strength=AuthStrength.STEPPED_UP,
        )


@pytest.fixture
def message_repo() -> InMemoryPatientMessageRepository:
    return InMemoryPatientMessageRepository()


@pytest.fixture(autouse=True)
def no_hooks():
    """No deployment callbacks unless a test registers one."""
    registry = get_patient_message_hook_registry()
    registry.clear()
    yield registry
    registry.clear()


@pytest.fixture
def patient_client(
    client: TestClient,
    message_repo: InMemoryPatientMessageRepository,
    monkeypatch: pytest.MonkeyPatch,
) -> TestClient:
    """The shared app client, with a patient front door and no database arming.

    The send limiter is a process-wide singleton keyed on the patient, so a
    file that sends as the same patient in test after test would otherwise
    hit the real limit part-way through and fail everything after it.
    """
    reset_patient_message_send_limiter()
    registry = PatientResolverRegistry()
    registry.register(_TwoPatientResolver())
    app.dependency_overrides[get_patient_resolver_registry] = lambda: registry
    app.dependency_overrides[get_patient_message_repository] = lambda: message_repo
    monkeypatch.setattr(patient_context_module, "get_db_session", object)
    monkeypatch.setattr(patient_context_module, "set_tenant_schema", lambda _s, _schema: None)
    monkeypatch.setattr(patient_context_module, "arm_current_patient_id", lambda _s, _p: None)
    return client


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _start(
    client: TestClient, token: str, *, subject: str | None = _SUBJECT, body: str = _BODY
) -> dict:
    response = client.post(
        PATIENT_BASE, json={"subject": subject, "body": body}, headers=_auth(token)
    )
    assert response.status_code == 201, response.text
    return response.json()


def _entries(audit: AuditService) -> list:
    return [call.args[0] for call in audit._repo.append.call_args_list]


# ---------------------------------------------------------------------------
# Patient surface
# ---------------------------------------------------------------------------


class TestPatientSends:
    def test_start_thread_returns_the_thread_and_its_first_message(
        self, patient_client: TestClient
    ) -> None:
        body = _start(patient_client, _TOKEN_A)

        assert body["subject"] == _SUBJECT
        assert body["status"] == "open"
        assert len(body["messages"]) == 1
        assert body["messages"][0]["sender"] == "patient"
        assert body["messages"][0]["body"] == _BODY

    def test_a_thread_can_start_without_a_subject(self, patient_client: TestClient) -> None:
        body = _start(patient_client, _TOKEN_A, subject=None)
        assert body["subject"] is None

    def test_sending_appends_to_the_thread(self, patient_client: TestClient) -> None:
        thread_id = _start(patient_client, _TOKEN_A)["id"]

        response = patient_client.post(
            f"{PATIENT_BASE}/{thread_id}/messages",
            json={"body": "and again today"},
            headers=_auth(_TOKEN_A),
        )
        assert response.status_code == 201, response.text

        detail = patient_client.get(f"{PATIENT_BASE}/{thread_id}", headers=_auth(_TOKEN_A))
        bodies = [m["body"] for m in detail.json()["messages"]]
        assert bodies == [_BODY, "and again today"]

    def test_a_patient_id_or_sender_in_the_body_is_ignored(
        self, patient_client: TestClient
    ) -> None:
        """There is no field to put them in, so a claim cannot take effect."""
        response = patient_client.post(
            PATIENT_BASE,
            json={
                "subject": _SUBJECT,
                "body": _BODY,
                "patient_id": _PATIENT_B,
                "sender": "clinician",
            },
            headers=_auth(_TOKEN_A),
        )
        assert response.status_code == 201, response.text
        assert response.json()["messages"][0]["sender"] == "patient"

        # And the row landed under A, not the B it claimed: B cannot see it.
        assert patient_client.get(PATIENT_BASE, headers=_auth(_TOKEN_B)).json()["total"] == 0
        assert patient_client.get(PATIENT_BASE, headers=_auth(_TOKEN_A)).json()["total"] == 1

    def test_over_length_body_is_rejected(self, patient_client: TestClient) -> None:
        response = patient_client.post(
            PATIENT_BASE,
            json={"body": "x" * (MAX_MESSAGE_BODY + 1)},
            headers=_auth(_TOKEN_A),
        )
        assert response.status_code == 422

    def test_empty_body_is_rejected(self, patient_client: TestClient) -> None:
        response = patient_client.post(PATIENT_BASE, json={"body": ""}, headers=_auth(_TOKEN_A))
        assert response.status_code == 422

    def test_send_is_rate_limited(self, patient_client: TestClient) -> None:
        def raise_429(key: str) -> None:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Too many requests. Please try again later.",
            )

        with patch("app.routes.patient_messages.get_patient_message_send_limiter") as limiter:
            limiter.return_value.check.side_effect = raise_429
            response = patient_client.post(
                PATIENT_BASE, json={"body": _BODY}, headers=_auth(_TOKEN_A)
            )

        assert response.status_code == 429

    def test_the_limiter_is_keyed_on_the_calling_patient(self, patient_client: TestClient) -> None:
        """Not on a clinician id, which a patient principal does not have."""
        with patch("app.routes.patient_messages.get_patient_message_send_limiter") as limiter:
            patient_client.post(PATIENT_BASE, json={"body": _BODY}, headers=_auth(_TOKEN_A))

        limiter.return_value.check.assert_called_once_with(_PATIENT_A)


class TestPatientReads:
    def test_list_carries_the_unread_count(
        self, patient_client: TestClient, message_repo: InMemoryPatientMessageRepository
    ) -> None:
        thread_id = _start(patient_client, _TOKEN_A)["id"]
        _seed_reply(message_repo, thread_id, _PATIENT_A)

        rows = patient_client.get(PATIENT_BASE, headers=_auth(_TOKEN_A)).json()["data"]

        assert len(rows) == 1
        # The patient's own message is never unread to them; the reply is.
        assert rows[0]["unread_count"] == 1

    def test_a_patient_cannot_see_another_patients_thread(self, patient_client: TestClient) -> None:
        thread_id = _start(patient_client, _TOKEN_A)["id"]

        # Control first: A can see it, so the refusal below is not vacuous.
        mine = patient_client.get(f"{PATIENT_BASE}/{thread_id}", headers=_auth(_TOKEN_A))
        assert mine.status_code == 200, mine.text

        response = patient_client.get(f"{PATIENT_BASE}/{thread_id}", headers=_auth(_TOKEN_B))
        assert response.status_code == 404

    def test_a_patient_cannot_send_into_another_patients_thread(
        self, patient_client: TestClient
    ) -> None:
        thread_id = _start(patient_client, _TOKEN_A)["id"]

        response = patient_client.post(
            f"{PATIENT_BASE}/{thread_id}/messages",
            json={"body": "not mine"},
            headers=_auth(_TOKEN_B),
        )
        assert response.status_code == 404

    def test_no_credential_is_401(self, patient_client: TestClient) -> None:
        assert patient_client.get(PATIENT_BASE).status_code == 401

    def test_an_unknown_bearer_is_401(self, patient_client: TestClient) -> None:
        """A clinician's token resolves to no patient principal."""
        response = patient_client.get(PATIENT_BASE, headers=_auth("a-clinician-session-token"))
        assert response.status_code == 401


class TestMarkRead:
    def test_marks_only_what_somebody_else_sent(
        self, patient_client: TestClient, message_repo: InMemoryPatientMessageRepository
    ) -> None:
        thread_id = _start(patient_client, _TOKEN_A)["id"]
        _seed_reply(message_repo, thread_id, _PATIENT_A)

        response = patient_client.post(f"{PATIENT_BASE}/{thread_id}/read", headers=_auth(_TOKEN_A))
        assert response.status_code == 200, response.text
        assert response.json()["marked_read"] == 1

        detail = patient_client.get(f"{PATIENT_BASE}/{thread_id}", headers=_auth(_TOKEN_A)).json()
        by_sender = {m["sender"]: m["read_at"] for m in detail["messages"]}
        assert by_sender["clinician"] is not None
        assert by_sender["patient"] is None

    def test_marking_twice_marks_nothing_the_second_time(
        self, patient_client: TestClient, message_repo: InMemoryPatientMessageRepository
    ) -> None:
        thread_id = _start(patient_client, _TOKEN_A)["id"]
        _seed_reply(message_repo, thread_id, _PATIENT_A)

        patient_client.post(f"{PATIENT_BASE}/{thread_id}/read", headers=_auth(_TOKEN_A))
        second = patient_client.post(f"{PATIENT_BASE}/{thread_id}/read", headers=_auth(_TOKEN_A))

        assert second.json()["marked_read"] == 0

    def test_another_patients_thread_is_404(self, patient_client: TestClient) -> None:
        thread_id = _start(patient_client, _TOKEN_A)["id"]
        response = patient_client.post(f"{PATIENT_BASE}/{thread_id}/read", headers=_auth(_TOKEN_B))
        assert response.status_code == 404


def _seed_reply(
    repo: InMemoryPatientMessageRepository, thread_id: str, patient_id: str, user_id: str = "u-1"
) -> None:
    """A clinician reply, put in directly so the patient tests need no grant."""
    repo.grant_access(patient_id, user_id)
    repo.add_reply(
        PatientMessage(
            id=f"reply-{thread_id}",
            thread_id=thread_id,
            patient_id=patient_id,
            sender="clinician",
            body="Let us get you seen this week.",
            created_at=datetime(2026, 9, 19, 12, 0, tzinfo=UTC),
        ),
        user_id,
    )


# ---------------------------------------------------------------------------
# Clinician surface
# ---------------------------------------------------------------------------


class TestClinicianSurface:
    def test_list_read_and_reply_with_a_grant(
        self,
        patient_client: TestClient,
        message_repo: InMemoryPatientMessageRepository,
        mock_user_id: str,
    ) -> None:
        thread_id = _start(patient_client, _TOKEN_A)["id"]
        message_repo.grant_access(_PATIENT_A, mock_user_id)

        listing = patient_client.get(f"/api/patients/{_PATIENT_A}/message-threads")
        assert listing.status_code == 200, listing.text
        assert listing.json()["total"] == 1

        detail = patient_client.get(f"/api/message-threads/{thread_id}")
        assert detail.status_code == 200, detail.text
        assert detail.json()["messages"][0]["body"] == _BODY

        reply = patient_client.post(
            f"/api/message-threads/{thread_id}/replies",
            json={"body": "Thanks for letting me know."},
        )
        assert reply.status_code == 201, reply.text
        assert reply.json()["sender"] == "clinician"

    def test_a_reply_bumps_last_message_at(
        self,
        patient_client: TestClient,
        message_repo: InMemoryPatientMessageRepository,
        mock_user_id: str,
    ) -> None:
        thread = _start(patient_client, _TOKEN_A)
        message_repo.grant_access(_PATIENT_A, mock_user_id)
        before = thread["last_message_at"]

        patient_client.post(
            f"/api/message-threads/{thread['id']}/replies", json={"body": "on my way"}
        )

        after = patient_client.get(f"/api/message-threads/{thread['id']}").json()
        assert after["last_message_at"] >= before
        assert after["messages"][-1]["sender"] == "clinician"

    def test_a_stranger_clinician_gets_404_not_403(
        self, patient_client: TestClient, message_repo: InMemoryPatientMessageRepository
    ) -> None:
        """No existence oracle: absent and forbidden look identical.

        The control is :meth:`test_list_read_and_reply_with_a_grant` — with a
        grant the same request succeeds, so this 404 is the access check and
        not a missing row.
        """
        thread_id = _start(patient_client, _TOKEN_A)["id"]
        # No grant issued for the default test user.
        assert patient_client.get(f"/api/message-threads/{thread_id}").status_code == 404
        assert (
            patient_client.post(
                f"/api/message-threads/{thread_id}/replies", json={"body": "nope"}
            ).status_code
            == 404
        )

    def test_a_stranger_clinicians_list_is_empty(self, patient_client: TestClient) -> None:
        _start(patient_client, _TOKEN_A)
        listing = patient_client.get(f"/api/patients/{_PATIENT_A}/message-threads")
        assert listing.status_code == 200
        assert listing.json()["total"] == 0

    def test_the_clinician_list_does_not_carry_unread_counts(
        self,
        patient_client: TestClient,
        message_repo: InMemoryPatientMessageRepository,
        mock_user_id: str,
    ) -> None:
        """Whether the patient has read something is not the clinician's row."""
        _start(patient_client, _TOKEN_A)
        message_repo.grant_access(_PATIENT_A, mock_user_id)

        rows = patient_client.get(f"/api/patients/{_PATIENT_A}/message-threads").json()["data"]

        assert rows[0]["unread_count"] is None


# ---------------------------------------------------------------------------
# Audit
# ---------------------------------------------------------------------------


class TestAudit:
    def test_a_patient_send_writes_patient_actor_rows(
        self, patient_client: TestClient, mock_audit_service: AuditService
    ) -> None:
        _start(patient_client, _TOKEN_A)

        entries = _entries(mock_audit_service)
        assert [e.action for e in entries] == [
            AuditAction.PATIENT_MESSAGE_THREAD_CREATED.value,
            AuditAction.PATIENT_MESSAGE_SENT.value,
        ]
        for entry in entries:
            assert entry.actor_type == ACTOR_TYPE_PATIENT
            assert entry.user_id == _PATIENT_A
            assert entry.patient_id == _PATIENT_A

    def test_mark_read_is_audited_with_a_count(
        self,
        patient_client: TestClient,
        message_repo: InMemoryPatientMessageRepository,
        mock_audit_service: AuditService,
    ) -> None:
        thread_id = _start(patient_client, _TOKEN_A)["id"]
        _seed_reply(message_repo, thread_id, _PATIENT_A)

        patient_client.post(f"{PATIENT_BASE}/{thread_id}/read", headers=_auth(_TOKEN_A))

        read_rows = [
            e
            for e in _entries(mock_audit_service)
            if e.action == AuditAction.PATIENT_MESSAGE_THREAD_READ.value
        ]
        assert len(read_rows) == 1
        assert read_rows[0].changes == {"marked_read": 1}
        assert read_rows[0].actor_type == ACTOR_TYPE_PATIENT

    def test_a_patient_reading_their_own_messages_writes_nothing(
        self, patient_client: TestClient, mock_audit_service: AuditService
    ) -> None:
        """Settled: reading your own record is not a disclosure."""
        thread_id = _start(patient_client, _TOKEN_A)["id"]
        before = len(_entries(mock_audit_service))

        patient_client.get(PATIENT_BASE, headers=_auth(_TOKEN_A))
        patient_client.get(f"{PATIENT_BASE}/{thread_id}", headers=_auth(_TOKEN_A))

        assert len(_entries(mock_audit_service)) == before

    def test_a_clinician_opening_a_thread_is_a_recorded_disclosure(
        self,
        patient_client: TestClient,
        message_repo: InMemoryPatientMessageRepository,
        mock_audit_service: AuditService,
        mock_user_id: str,
    ) -> None:
        thread_id = _start(patient_client, _TOKEN_A)["id"]
        message_repo.grant_access(_PATIENT_A, mock_user_id)

        patient_client.get(f"/api/message-threads/{thread_id}")

        viewed = [
            e
            for e in _entries(mock_audit_service)
            if e.action == AuditAction.PATIENT_MESSAGE_THREAD_VIEWED.value
        ]
        assert len(viewed) == 1
        assert viewed[0].actor_type == ACTOR_TYPE_CLINICIAN
        assert viewed[0].resource_id == thread_id
        assert viewed[0].patient_id == _PATIENT_A

    def test_a_clinician_reply_is_audited(
        self,
        patient_client: TestClient,
        message_repo: InMemoryPatientMessageRepository,
        mock_audit_service: AuditService,
        mock_user_id: str,
    ) -> None:
        thread_id = _start(patient_client, _TOKEN_A)["id"]
        message_repo.grant_access(_PATIENT_A, mock_user_id)

        patient_client.post(
            f"/api/message-threads/{thread_id}/replies", json={"body": "acknowledged"}
        )

        sends = [
            e
            for e in _entries(mock_audit_service)
            if e.action == AuditAction.PATIENT_MESSAGE_SENT.value
        ]
        assert sends[-1].actor_type == ACTOR_TYPE_CLINICIAN
        assert sends[-1].changes["sender"] == "clinician"

    def test_no_audit_payload_or_log_record_carries_the_words(
        self,
        patient_client: TestClient,
        message_repo: InMemoryPatientMessageRepository,
        mock_audit_service: AuditService,
        mock_user_id: str,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """S3 / guardrail #5: the store holds the words, nothing else does."""
        with caplog.at_level(logging.DEBUG):
            thread_id = _start(patient_client, _TOKEN_A)["id"]
            message_repo.grant_access(_PATIENT_A, mock_user_id)
            patient_client.get(f"/api/message-threads/{thread_id}")
            patient_client.post(
                f"/api/message-threads/{thread_id}/replies", json={"body": "acknowledged"}
            )

        rendered = "\n".join(str(e.changes) for e in _entries(mock_audit_service))
        logged = "\n".join(record.getMessage() for record in caplog.records)
        for secret in (_BODY, _SUBJECT, "acknowledged"):
            assert secret not in rendered
            assert secret not in logged

    def test_every_action_in_use_is_named(self, patient_client: TestClient) -> None:
        """A rename of an enum member would otherwise pass every test above."""
        assert AuditAction.PATIENT_MESSAGE_THREAD_CREATED.value == "patient_message_thread_created"
        assert AuditAction.PATIENT_MESSAGE_SENT.value == "patient_message_sent"
        assert AuditAction.PATIENT_MESSAGE_THREAD_READ.value == "patient_message_thread_read"
        assert AuditAction.PATIENT_MESSAGE_THREAD_VIEWED.value == "patient_message_thread_viewed"


# ---------------------------------------------------------------------------
# The post-message seam, through the route
# ---------------------------------------------------------------------------


class TestHookDispatchFromRoutes:
    def test_a_patient_send_reaches_a_registered_hook(
        self, patient_client: TestClient, no_hooks
    ) -> None:
        seen: list = []
        no_hooks.register(seen.append)

        thread = _start(patient_client, _TOKEN_A)

        assert len(seen) == 1
        event = seen[0]
        assert event.sender == "patient"
        assert event.thread_id == thread["id"]
        assert event.patient_id == _PATIENT_A
        assert event.practice_schema == "practice_test"
        assert event.subject == _SUBJECT
        assert event.body == _BODY

    def test_a_clinician_reply_reaches_it_too(
        self,
        patient_client: TestClient,
        message_repo: InMemoryPatientMessageRepository,
        mock_user_id: str,
        no_hooks,
    ) -> None:
        thread_id = _start(patient_client, _TOKEN_A)["id"]
        message_repo.grant_access(_PATIENT_A, mock_user_id)
        seen: list = []
        no_hooks.register(seen.append)

        patient_client.post(f"/api/message-threads/{thread_id}/replies", json={"body": "on it"})

        assert [e.sender for e in seen] == ["clinician"]

    def test_a_raising_hook_still_lets_the_send_succeed(
        self, patient_client: TestClient, no_hooks
    ) -> None:
        def explode(event) -> None:
            raise RuntimeError("downstream is down")

        no_hooks.register(explode)

        response = patient_client.post(PATIENT_BASE, json={"body": _BODY}, headers=_auth(_TOKEN_A))

        assert response.status_code == 201

    def test_one_dispatch_per_send(self, patient_client: TestClient, no_hooks) -> None:
        seen: list = []
        no_hooks.register(seen.append)

        thread_id = _start(patient_client, _TOKEN_A)["id"]
        patient_client.post(
            f"{PATIENT_BASE}/{thread_id}/messages",
            json={"body": "again"},
            headers=_auth(_TOKEN_A),
        )

        assert len(seen) == 2
