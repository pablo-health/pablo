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

import inspect
import logging
from datetime import UTC, datetime
from pathlib import Path
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
from app.auth.service import require_baa_acceptance
from app.main import app
from app.models import Patient, PatientMessage
from app.models.audit import ACTOR_TYPE_CLINICIAN, ACTOR_TYPE_PATIENT, AuditAction
from app.models.patient_message_api import MAX_MESSAGE_BODY
from app.rate_limit import reset_patient_message_send_limiter
from app.repositories import InMemoryPatientMessageRepository, InMemoryPatientRepository
from app.routes import patient_messages
from app.routes.patient_messages import (
    CLOSED_THREAD_MESSAGE,
    get_clinician_patient_repository,
    get_patient_message_repository,
)
from app.services.patient_message_hooks import get_patient_message_hook_registry
from app.utcnow import utc_now
from fastapi import HTTPException, params, status

if TYPE_CHECKING:
    from collections.abc import Callable

    from app.services import AuditService
    from fastapi.testclient import TestClient

_PATIENT_A = "patient-a"
_PATIENT_B = "patient-b"
_TOKEN_A = "credential-of-patient-a"
_TOKEN_B = "credential-of-patient-b"

# A second clinician in the practice, used as an assignee. Assignment is
# routing, so nothing here grants them access and nothing here expects any.
_OTHER_CLINICIAN = "0c3b7d1e-5a92-4f60-b8c4-2d9e7a105f38"

# The clinician who "created" patient A's chart in the in-memory patient
# repository below — never the caller under test, so patient A exists
# without granting the caller anything by default. Each clinician-surface
# test says out loud who holds a grant, mirroring how ``message_repo`` is
# used throughout this file.
_CHART_CREATOR = "someone-elses-clinician"

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


@pytest.fixture
def patient_repo() -> InMemoryPatientRepository:
    """The clinician-facing chart the message-list route checks against.

    Patient A exists, created by a different clinician, so a grant on it is
    never presumed — a test that wants the caller to hold one calls
    ``grant_access`` explicitly, same as ``message_repo``.
    """
    repo = InMemoryPatientRepository()
    now = utc_now()
    repo.create(
        Patient(
            id=_PATIENT_A,
            first_name="Ada",
            last_name="Lovelace",
            created_at=now,
            updated_at=now,
        ),
        _CHART_CREATOR,
    )
    return repo


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
    patient_repo: InMemoryPatientRepository,
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
    app.dependency_overrides[get_clinician_patient_repository] = lambda: patient_repo
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
        patient_repo: InMemoryPatientRepository,
        mock_user_id: str,
    ) -> None:
        thread_id = _start(patient_client, _TOKEN_A)["id"]
        message_repo.grant_access(_PATIENT_A, mock_user_id)
        patient_repo.grant_access(_PATIENT_A, mock_user_id)

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

    def test_a_stranger_clinicians_list_is_404(self, patient_client: TestClient) -> None:
        """No existence oracle here either: a patient the caller has no
        grant on reads the same as one that does not exist. The control is
        :meth:`test_list_read_and_reply_with_a_grant` — with a grant the
        same id gives a 200."""
        _start(patient_client, _TOKEN_A)
        listing = patient_client.get(f"/api/patients/{_PATIENT_A}/message-threads")
        assert listing.status_code == 404

    def test_a_foreign_patient_id_is_404(self, patient_client: TestClient) -> None:
        """An id naming no patient this chart knows about at all — the same
        404 as one that exists but belongs to someone else's chart."""
        resp = patient_client.get("/api/patients/patient-from-another-practice/message-threads")
        assert resp.status_code == 404

    def test_the_clinician_list_counts_what_the_patient_sent(
        self,
        patient_client: TestClient,
        message_repo: InMemoryPatientMessageRepository,
        patient_repo: InMemoryPatientRepository,
        mock_user_id: str,
    ) -> None:
        """A different count from the patient's, measured from a different mark.

        The patient's own count is what the practice sent them and they have
        not opened. This one is what they sent that nobody here has looked at.
        """
        thread_id = _start(patient_client, _TOKEN_A)["id"]
        _seed_reply(message_repo, thread_id, _PATIENT_A, user_id=mock_user_id)
        patient_repo.grant_access(_PATIENT_A, mock_user_id)

        rows = patient_client.get(f"/api/patients/{_PATIENT_A}/message-threads").json()["data"]

        # One patient message, and the practice has never marked the thread
        # read — so the reply the practice itself wrote does not count.
        assert rows[0]["unread_count"] == 1


# ---------------------------------------------------------------------------
# Lifecycle: close, reopen, and what a closed thread refuses
# ---------------------------------------------------------------------------


class TestThreadLifecycle:
    def test_close_records_when_and_who(
        self,
        patient_client: TestClient,
        message_repo: InMemoryPatientMessageRepository,
        mock_user_id: str,
    ) -> None:
        thread_id = _start(patient_client, _TOKEN_A)["id"]
        message_repo.grant_access(_PATIENT_A, mock_user_id)

        response = patient_client.post(f"/api/message-threads/{thread_id}/close")

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["status"] == "closed"
        assert body["closed_at"] is not None
        assert body["closed_by"] == mock_user_id

    def test_closing_twice_keeps_the_first_closure(
        self,
        patient_client: TestClient,
        message_repo: InMemoryPatientMessageRepository,
        mock_user_id: str,
    ) -> None:
        """Who ended the conversation is the fact, not who pressed last."""
        thread_id = _start(patient_client, _TOKEN_A)["id"]
        message_repo.grant_access(_PATIENT_A, mock_user_id)

        first = patient_client.post(f"/api/message-threads/{thread_id}/close").json()
        second = patient_client.post(f"/api/message-threads/{thread_id}/close").json()

        assert second["closed_at"] == first["closed_at"]
        assert second["closed_by"] == first["closed_by"]

    def test_reopen_clears_the_closure(
        self,
        patient_client: TestClient,
        message_repo: InMemoryPatientMessageRepository,
        mock_user_id: str,
    ) -> None:
        thread_id = _start(patient_client, _TOKEN_A)["id"]
        message_repo.grant_access(_PATIENT_A, mock_user_id)
        patient_client.post(f"/api/message-threads/{thread_id}/close")

        body = patient_client.post(f"/api/message-threads/{thread_id}/reopen").json()

        assert body["status"] == "open"
        assert body["closed_at"] is None
        assert body["closed_by"] is None

    def test_a_reply_reopens_a_closed_thread_in_one_request(
        self,
        patient_client: TestClient,
        message_repo: InMemoryPatientMessageRepository,
        mock_user_id: str,
    ) -> None:
        """One action. The patient never holds an answer they cannot answer."""
        thread_id = _start(patient_client, _TOKEN_A)["id"]
        message_repo.grant_access(_PATIENT_A, mock_user_id)
        patient_client.post(f"/api/message-threads/{thread_id}/close")

        reply = patient_client.post(
            f"/api/message-threads/{thread_id}/replies", json={"body": "one more thing"}
        )

        assert reply.status_code == 201, reply.text
        after = patient_client.get(f"/api/message-threads/{thread_id}").json()
        assert after["status"] == "open"
        assert after["closed_at"] is None
        assert after["messages"][-1]["body"] == "one more thing"

    def test_a_patient_writing_into_a_closed_thread_is_refused(
        self,
        patient_client: TestClient,
        message_repo: InMemoryPatientMessageRepository,
        mock_user_id: str,
    ) -> None:
        thread_id = _start(patient_client, _TOKEN_A)["id"]
        message_repo.grant_access(_PATIENT_A, mock_user_id)
        patient_client.post(f"/api/message-threads/{thread_id}/close")

        response = patient_client.post(
            f"{PATIENT_BASE}/{thread_id}/messages",
            json={"body": "are you still there"},
            headers=_auth(_TOKEN_A),
        )

        assert response.status_code == 409
        assert response.json()["error"]["message"] == CLOSED_THREAD_MESSAGE

    def test_a_patient_can_still_start_a_new_thread(
        self,
        patient_client: TestClient,
        message_repo: InMemoryPatientMessageRepository,
        mock_user_id: str,
    ) -> None:
        """What the 409 tells them to do has to work, or the copy is a lie."""
        thread_id = _start(patient_client, _TOKEN_A)["id"]
        message_repo.grant_access(_PATIENT_A, mock_user_id)
        patient_client.post(f"/api/message-threads/{thread_id}/close")

        fresh = _start(patient_client, _TOKEN_A, subject="something else")

        assert fresh["status"] == "open"
        assert fresh["id"] != thread_id

    def test_the_patients_payload_carries_the_status(
        self,
        patient_client: TestClient,
        message_repo: InMemoryPatientMessageRepository,
        mock_user_id: str,
    ) -> None:
        """The portal needs it to render the closed state, so it is pinned."""
        thread_id = _start(patient_client, _TOKEN_A)["id"]
        message_repo.grant_access(_PATIENT_A, mock_user_id)
        patient_client.post(f"/api/message-threads/{thread_id}/close")

        detail = patient_client.get(f"{PATIENT_BASE}/{thread_id}", headers=_auth(_TOKEN_A)).json()
        listed = patient_client.get(PATIENT_BASE, headers=_auth(_TOKEN_A)).json()["data"]

        assert detail["status"] == "closed"
        assert listed[0]["status"] == "closed"

    def test_a_stranger_clinician_cannot_close_or_reopen(
        self, patient_client: TestClient, message_repo: InMemoryPatientMessageRepository
    ) -> None:
        """Control: :meth:`test_close_records_when_and_who` does the same with a grant."""
        thread_id = _start(patient_client, _TOKEN_A)["id"]

        assert patient_client.post(f"/api/message-threads/{thread_id}/close").status_code == 404
        assert patient_client.post(f"/api/message-threads/{thread_id}/reopen").status_code == 404


# ---------------------------------------------------------------------------
# Assignment
# ---------------------------------------------------------------------------


class TestAssignment:
    def test_assigning_and_unassigning_round_trip(
        self,
        patient_client: TestClient,
        message_repo: InMemoryPatientMessageRepository,
        mock_user_id: str,
    ) -> None:
        thread_id = _start(patient_client, _TOKEN_A)["id"]
        message_repo.grant_access(_PATIENT_A, mock_user_id)

        assigned = patient_client.post(
            f"/api/message-threads/{thread_id}/assign", json={"user_id": _OTHER_CLINICIAN}
        )
        assert assigned.status_code == 200, assigned.text
        assert assigned.json()["assigned_user_id"] == _OTHER_CLINICIAN

        cleared = patient_client.post(
            f"/api/message-threads/{thread_id}/assign", json={"user_id": None}
        )
        assert cleared.json()["assigned_user_id"] is None

    def test_assignment_does_not_change_who_can_read(
        self,
        patient_client: TestClient,
        message_repo: InMemoryPatientMessageRepository,
        mock_user_id: str,
    ) -> None:
        """Assigned away, still readable by anybody with a grant."""
        thread_id = _start(patient_client, _TOKEN_A)["id"]
        message_repo.grant_access(_PATIENT_A, mock_user_id)
        patient_client.post(
            f"/api/message-threads/{thread_id}/assign", json={"user_id": _OTHER_CLINICIAN}
        )

        detail = patient_client.get(f"/api/message-threads/{thread_id}")
        reply = patient_client.post(
            f"/api/message-threads/{thread_id}/replies", json={"body": "covering today"}
        )

        assert detail.status_code == 200
        assert reply.status_code == 201

    def test_the_filter_narrows_the_list_and_nothing_else(
        self,
        patient_client: TestClient,
        message_repo: InMemoryPatientMessageRepository,
        patient_repo: InMemoryPatientRepository,
        mock_user_id: str,
    ) -> None:
        mine = _start(patient_client, _TOKEN_A, subject="mine")["id"]
        theirs = _start(patient_client, _TOKEN_A, subject="theirs")["id"]
        loose = _start(patient_client, _TOKEN_A, subject="loose")["id"]
        message_repo.grant_access(_PATIENT_A, mock_user_id)
        patient_repo.grant_access(_PATIENT_A, mock_user_id)
        patient_client.post(f"/api/message-threads/{mine}/assign", json={"user_id": mock_user_id})
        patient_client.post(
            f"/api/message-threads/{theirs}/assign", json={"user_id": _OTHER_CLINICIAN}
        )

        listing = f"/api/patients/{_PATIENT_A}/message-threads"
        all_ids = {t["id"] for t in patient_client.get(listing).json()["data"]}
        mine_ids = {t["id"] for t in patient_client.get(f"{listing}?assigned=me").json()["data"]}
        loose_ids = {
            t["id"] for t in patient_client.get(f"{listing}?assigned=unassigned").json()["data"]
        }

        assert all_ids == {mine, theirs, loose}
        assert mine_ids == {mine}
        assert loose_ids == {loose}
        # Filtered out of the list, still readable by id.
        assert patient_client.get(f"/api/message-threads/{theirs}").status_code == 200

    def test_an_unknown_filter_value_is_rejected(
        self,
        patient_client: TestClient,
        message_repo: InMemoryPatientMessageRepository,
        mock_user_id: str,
    ) -> None:
        _start(patient_client, _TOKEN_A)
        message_repo.grant_access(_PATIENT_A, mock_user_id)

        response = patient_client.get(
            f"/api/patients/{_PATIENT_A}/message-threads?assigned=everyone"
        )

        assert response.status_code == 422


# ---------------------------------------------------------------------------
# The practice's own read mark
# ---------------------------------------------------------------------------


class TestClinicianUnread:
    def test_marking_read_zeroes_the_count(
        self,
        patient_client: TestClient,
        message_repo: InMemoryPatientMessageRepository,
        patient_repo: InMemoryPatientRepository,
        mock_user_id: str,
    ) -> None:
        thread_id = _start(patient_client, _TOKEN_A)["id"]
        message_repo.grant_access(_PATIENT_A, mock_user_id)
        patient_repo.grant_access(_PATIENT_A, mock_user_id)
        listing = f"/api/patients/{_PATIENT_A}/message-threads"
        assert patient_client.get(listing).json()["data"][0]["unread_count"] == 1

        marked = patient_client.post(f"/api/message-threads/{thread_id}/read")

        assert marked.status_code == 200, marked.text
        assert patient_client.get(listing).json()["data"][0]["unread_count"] == 0

    def test_a_later_patient_message_counts_again(
        self,
        patient_client: TestClient,
        message_repo: InMemoryPatientMessageRepository,
        patient_repo: InMemoryPatientRepository,
        mock_user_id: str,
    ) -> None:
        thread_id = _start(patient_client, _TOKEN_A)["id"]
        message_repo.grant_access(_PATIENT_A, mock_user_id)
        patient_repo.grant_access(_PATIENT_A, mock_user_id)
        patient_client.post(f"/api/message-threads/{thread_id}/read")

        patient_client.post(
            f"{PATIENT_BASE}/{thread_id}/messages",
            json={"body": "one more thing"},
            headers=_auth(_TOKEN_A),
        )

        listing = f"/api/patients/{_PATIENT_A}/message-threads"
        assert patient_client.get(listing).json()["data"][0]["unread_count"] == 1

    def test_the_practices_mark_leaves_the_patients_read_state_alone(
        self,
        patient_client: TestClient,
        message_repo: InMemoryPatientMessageRepository,
        mock_user_id: str,
    ) -> None:
        """Two different facts, two different columns."""
        thread_id = _start(patient_client, _TOKEN_A)["id"]
        _seed_reply(message_repo, thread_id, _PATIENT_A, user_id=mock_user_id)

        patient_client.post(f"/api/message-threads/{thread_id}/read")

        rows = patient_client.get(PATIENT_BASE, headers=_auth(_TOKEN_A)).json()["data"]
        assert rows[0]["unread_count"] == 1

    def test_a_stranger_clinician_cannot_mark_read(
        self, patient_client: TestClient, message_repo: InMemoryPatientMessageRepository
    ) -> None:
        thread_id = _start(patient_client, _TOKEN_A)["id"]
        assert patient_client.post(f"/api/message-threads/{thread_id}/read").status_code == 404


# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------


class TestExport:
    def test_export_carries_the_thread_and_every_message(
        self,
        patient_client: TestClient,
        message_repo: InMemoryPatientMessageRepository,
        mock_user_id: str,
    ) -> None:
        thread_id = _start(patient_client, _TOKEN_A)["id"]
        _seed_reply(message_repo, thread_id, _PATIENT_A, user_id=mock_user_id)

        response = patient_client.get(f"/api/message-threads/{thread_id}/export")

        assert response.status_code == 200, response.text
        assert response.headers["content-type"].startswith("application/json")
        body = response.json()
        assert body["thread"]["id"] == thread_id
        assert body["thread"]["status"] == "open"
        assert {m["sender"] for m in body["messages"]} == {"patient", "clinician"}
        sent = [m["created_at"] for m in body["messages"]]
        assert sent == sorted(sent), "a transcript reads oldest first"
        for message in body["messages"]:
            assert set(message) >= {"sender", "created_at", "body", "read_at"}

    def test_a_stranger_clinician_cannot_export(
        self, patient_client: TestClient, message_repo: InMemoryPatientMessageRepository
    ) -> None:
        """Control: the test above exports the same thread with a grant."""
        thread_id = _start(patient_client, _TOKEN_A)["id"]
        assert patient_client.get(f"/api/message-threads/{thread_id}/export").status_code == 404


# ---------------------------------------------------------------------------
# The patient surface has none of this
# ---------------------------------------------------------------------------


class TestNothingDeletesCorrespondence:
    """Retention is the chart's retention, and no route shortens it.

    Read as a claim about the whole surface rather than about one handler:
    a delete route added later would pass every other test in this file, so
    the absence is asserted over the module's source.
    """

    def test_no_route_on_this_surface_deletes(self) -> None:
        source = Path(patient_messages.__file__).read_text(encoding="utf-8")
        assert ".delete(" not in source
        assert "DELETE" not in source

    def test_the_repository_offers_no_delete_verb(self) -> None:
        verbs = [name for name in dir(InMemoryPatientMessageRepository) if "delete" in name.lower()]
        assert verbs == []


class TestPatientCannotReachTheClinicianSurface:
    """None of the lifecycle is reachable with a patient's credential.

    Two halves, because either alone would be reassuring and wrong. The
    patient front door does not mount these paths at all, so there is
    nothing for a patient session token to address; and every one of them
    is behind the clinician dependency, which a patient token does not
    satisfy. Asserting the dependency by inspection rather than by calling
    the route is deliberate: this suite overrides the whole clinician auth
    chain, so a request-level 401 here would be testing the override.
    """

    @pytest.mark.parametrize("path", ["/close", "/reopen", "/assign", "/export"])
    def test_the_patient_router_does_not_mount_it(
        self, patient_client: TestClient, path: str
    ) -> None:
        thread_id = _start(patient_client, _TOKEN_A)["id"]

        response = patient_client.post(
            f"{PATIENT_BASE}/{thread_id}{path}", json={}, headers=_auth(_TOKEN_A)
        )

        assert response.status_code in (404, 405)

    @pytest.mark.parametrize(
        "handler",
        [
            patient_messages.close_thread,
            patient_messages.reopen_thread,
            patient_messages.assign_thread,
            patient_messages.mark_thread_read_by_clinician,
            patient_messages.export_thread,
        ],
    )
    def test_every_lifecycle_route_sits_behind_the_clinician_dependency(
        self, handler: Callable[..., object]
    ) -> None:
        dependencies = {
            parameter.default.dependency
            for parameter in inspect.signature(handler).parameters.values()
            if isinstance(parameter.default, params.Depends)
        }
        assert require_baa_acceptance in dependencies


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

    def test_a_clinicians_list_read_is_audited_by_patient(
        self,
        patient_client: TestClient,
        message_repo: InMemoryPatientMessageRepository,
        patient_repo: InMemoryPatientRepository,
        mock_audit_service: AuditService,
        mock_user_id: str,
    ) -> None:
        _start(patient_client, _TOKEN_A)
        message_repo.grant_access(_PATIENT_A, mock_user_id)
        patient_repo.grant_access(_PATIENT_A, mock_user_id)

        patient_client.get(f"/api/patients/{_PATIENT_A}/message-threads")

        viewed = [
            e
            for e in _entries(mock_audit_service)
            if e.action == AuditAction.PATIENT_MESSAGE_THREAD_VIEWED.value
        ]
        assert len(viewed) == 1
        assert viewed[0].actor_type == ACTOR_TYPE_CLINICIAN
        assert viewed[0].resource_id == _PATIENT_A
        assert viewed[0].patient_id == _PATIENT_A
        assert viewed[0].changes == {"thread_count": 1, "assigned": "all"}

    def test_a_refused_clinician_listing_writes_no_audit_row(
        self, patient_client: TestClient, mock_audit_service: AuditService
    ) -> None:
        """The control above shows a granted read audits; this id has no
        grant, so nothing about it should land on the log at all."""
        before = len(_entries(mock_audit_service))

        resp = patient_client.get(f"/api/patients/{_PATIENT_A}/message-threads")

        assert resp.status_code == 404
        assert len(_entries(mock_audit_service)) == before

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
        assert AuditAction.PATIENT_MESSAGE_THREAD_CLOSED.value == "patient_message_thread_closed"
        assert (
            AuditAction.PATIENT_MESSAGE_THREAD_REOPENED.value == "patient_message_thread_reopened"
        )
        assert (
            AuditAction.PATIENT_MESSAGE_THREAD_ASSIGNED.value == "patient_message_thread_assigned"
        )
        assert (
            AuditAction.PATIENT_MESSAGE_THREAD_EXPORTED.value == "patient_message_thread_exported"
        )


class TestLifecycleAudit:
    """Every lifecycle write is one clinician row, and none of them carry words."""

    @pytest.fixture
    def granted_thread(
        self,
        patient_client: TestClient,
        message_repo: InMemoryPatientMessageRepository,
        mock_user_id: str,
    ) -> str:
        thread_id = _start(patient_client, _TOKEN_A)["id"]
        message_repo.grant_access(_PATIENT_A, mock_user_id)
        return thread_id

    def _only(self, audit: AuditService, action: AuditAction) -> object:
        rows = [e for e in _entries(audit) if e.action == action.value]
        assert len(rows) == 1
        assert rows[0].actor_type == ACTOR_TYPE_CLINICIAN
        assert rows[0].patient_id == _PATIENT_A
        return rows[0]

    def test_close_and_reopen_each_write_one_row(
        self,
        patient_client: TestClient,
        granted_thread: str,
        mock_audit_service: AuditService,
    ) -> None:
        patient_client.post(f"/api/message-threads/{granted_thread}/close")
        patient_client.post(f"/api/message-threads/{granted_thread}/reopen")

        closed = self._only(mock_audit_service, AuditAction.PATIENT_MESSAGE_THREAD_CLOSED)
        reopened = self._only(mock_audit_service, AuditAction.PATIENT_MESSAGE_THREAD_REOPENED)
        assert closed.resource_id == granted_thread  # type: ignore[attr-defined]
        assert reopened.changes == {"by": "request"}  # type: ignore[attr-defined]

    def test_a_reply_that_reopens_writes_both_facts(
        self,
        patient_client: TestClient,
        granted_thread: str,
        mock_audit_service: AuditService,
    ) -> None:
        patient_client.post(f"/api/message-threads/{granted_thread}/close")
        patient_client.post(
            f"/api/message-threads/{granted_thread}/replies", json={"body": "one more thing"}
        )

        actions = [e.action for e in _entries(mock_audit_service)]
        assert actions[-2:] == [
            AuditAction.PATIENT_MESSAGE_SENT.value,
            AuditAction.PATIENT_MESSAGE_THREAD_REOPENED.value,
        ]

    def test_a_reply_into_an_open_thread_reopens_nothing(
        self,
        patient_client: TestClient,
        granted_thread: str,
        mock_audit_service: AuditService,
    ) -> None:
        patient_client.post(
            f"/api/message-threads/{granted_thread}/replies", json={"body": "on it"}
        )

        actions = [e.action for e in _entries(mock_audit_service)]
        assert AuditAction.PATIENT_MESSAGE_THREAD_REOPENED.value not in actions

    def test_assign_records_who_was_asked(
        self,
        patient_client: TestClient,
        granted_thread: str,
        mock_audit_service: AuditService,
    ) -> None:
        patient_client.post(
            f"/api/message-threads/{granted_thread}/assign", json={"user_id": _OTHER_CLINICIAN}
        )

        entry = self._only(mock_audit_service, AuditAction.PATIENT_MESSAGE_THREAD_ASSIGNED)
        assert entry.changes == {"assigned_user_id": _OTHER_CLINICIAN}  # type: ignore[attr-defined]

    def test_export_records_the_disclosure_with_a_count(
        self,
        patient_client: TestClient,
        granted_thread: str,
        mock_audit_service: AuditService,
    ) -> None:
        patient_client.get(f"/api/message-threads/{granted_thread}/export")

        entry = self._only(mock_audit_service, AuditAction.PATIENT_MESSAGE_THREAD_EXPORTED)
        assert entry.changes == {"message_count": 1}  # type: ignore[attr-defined]

    def test_the_practices_read_mark_is_a_clinician_row(
        self,
        patient_client: TestClient,
        granted_thread: str,
        mock_audit_service: AuditService,
    ) -> None:
        """Same action as the patient's mark-read, separated by actor."""
        patient_client.post(f"/api/message-threads/{granted_thread}/read")

        rows = [
            e
            for e in _entries(mock_audit_service)
            if e.action == AuditAction.PATIENT_MESSAGE_THREAD_READ.value
        ]
        assert len(rows) == 1
        assert rows[0].actor_type == ACTOR_TYPE_CLINICIAN

    def test_no_lifecycle_payload_or_log_record_carries_the_words(
        self,
        patient_client: TestClient,
        granted_thread: str,
        mock_audit_service: AuditService,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        with caplog.at_level(logging.DEBUG):
            patient_client.post(f"/api/message-threads/{granted_thread}/close")
            patient_client.post(
                f"/api/message-threads/{granted_thread}/replies", json={"body": "acknowledged"}
            )
            patient_client.post(
                f"/api/message-threads/{granted_thread}/assign", json={"user_id": _OTHER_CLINICIAN}
            )
            patient_client.post(f"/api/message-threads/{granted_thread}/read")
            patient_client.get(f"/api/message-threads/{granted_thread}/export")

        rendered = "\n".join(str(e.changes) for e in _entries(mock_audit_service))
        logged = "\n".join(record.getMessage() for record in caplog.records)
        for secret in (_BODY, _SUBJECT, "acknowledged"):
            assert secret not in rendered
            assert secret not in logged


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
