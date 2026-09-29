# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""The practice's inbox: every conversation, and every message, in one place.

Three routes, all clinician-side, all scoped by the caller's grants:

* ``GET /api/message-threads`` — conversations, grouped: unread first, then
  newest, with whose each one is and no message text.
* ``GET /api/message-threads/messages`` — the same inbox ungrouped: each
  message a client sent on its own row, newest first.
* ``GET /api/message-threads/unread-count`` — one number, for a badge.

What these tests are for, in order of how much they would hurt to get wrong:

* A patient the caller has no grant on contributes nothing — no row, no
  count, no audit entry naming them.
* The grouped list carries no words; the ungrouped one does, and is recorded
  as the content read it is.
* The order is the one a practice works in.

The Postgres half — the same scoping as SQL, and a deleted patient left out —
is in ``tests_integration/database/test_patient_message_inbox_db.py``.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import pytest
from app.main import app
from app.models import PatientMessage, PatientMessageThread
from app.models.audit import AuditAction
from app.models.patient_message import SENDER_CLINICIAN, SENDER_PATIENT
from app.repositories import InMemoryPatientMessageRepository
from app.routes.patient_messages import get_patient_message_repository

if TYPE_CHECKING:
    from app.services import AuditService
    from fastapi.testclient import TestClient

INBOX = "/api/message-threads"
MESSAGES = "/api/message-threads/messages"
UNREAD = "/api/message-threads/unread-count"

_ADA = "11111111-1111-4111-8111-111111111111"
_GRACE = "22222222-2222-4222-8222-222222222222"
_STRANGER = "33333333-3333-4333-8333-333333333333"
_T0 = datetime(2026, 9, 1, 9, 0, tzinfo=UTC)


@pytest.fixture
def repo(mock_user_id: str) -> InMemoryPatientMessageRepository:
    """Two patients the caller may see, and one they may not."""
    repo = InMemoryPatientMessageRepository()
    repo.name_patient(_ADA, "Ada Lovelace")
    repo.name_patient(_GRACE, "Grace Hopper")
    repo.name_patient(_STRANGER, "Someone Else")
    repo.grant_access(_ADA, mock_user_id)
    repo.grant_access(_GRACE, mock_user_id)
    return repo


@pytest.fixture
def inbox(client: TestClient, repo: InMemoryPatientMessageRepository) -> TestClient:
    app.dependency_overrides[get_patient_message_repository] = lambda: repo
    return client


def _thread(
    repo: InMemoryPatientMessageRepository,
    patient_id: str,
    thread_id: str,
    *,
    at: datetime,
    subject: str | None = None,
    body: str = "hello",
) -> None:
    thread = PatientMessageThread(
        id=thread_id,
        patient_id=patient_id,
        subject=subject,
        status="open",
        created_at=at,
        last_message_at=at,
    )
    message = PatientMessage(
        id=f"{thread_id}-m1",
        thread_id=thread_id,
        patient_id=patient_id,
        sender=SENDER_PATIENT,
        body=body,
        created_at=at,
    )
    repo.add_patient_thread(thread, message)


def _say(
    repo: InMemoryPatientMessageRepository,
    patient_id: str,
    thread_id: str,
    message_id: str,
    *,
    at: datetime,
    body: str,
    sender: str = SENDER_PATIENT,
) -> None:
    repo.add_patient_message(
        PatientMessage(
            id=message_id,
            thread_id=thread_id,
            patient_id=patient_id,
            sender=sender,
            body=body,
            created_at=at,
        )
    )


def _entries(audit: AuditService) -> list:
    return [call.args[0] for call in audit._repo.append.call_args_list]


# ── grouped: conversations ──────────────────────────────────────────────


class TestConversations:
    def test_unread_first_then_newest_with_whose_it_is(
        self, inbox: TestClient, repo: InMemoryPatientMessageRepository, mock_user_id: str
    ) -> None:
        _thread(repo, _ADA, "t-old-unread", at=_T0, subject="refill")
        _thread(repo, _GRACE, "t-new-read", at=_T0 + timedelta(hours=2))
        repo.mark_thread_read_by_clinician("t-new-read", mock_user_id, _T0 + timedelta(hours=3))
        _thread(repo, _GRACE, "t-mid-unread", at=_T0 + timedelta(hours=1))

        body = inbox.get(INBOX).json()

        assert [row["id"] for row in body["data"]] == ["t-mid-unread", "t-old-unread", "t-new-read"]
        first = body["data"][1]
        assert first["patient_id"] == _ADA
        assert first["patient_name"] == "Ada Lovelace"
        assert first["subject"] == "refill"
        assert first["unread_count"] == 1
        assert body["has_more"] is False

    def test_carries_no_message_text(
        self, inbox: TestClient, repo: InMemoryPatientMessageRepository
    ) -> None:
        _thread(repo, _ADA, "t1", at=_T0, body="the words stay in the thread")

        assert "the words stay in the thread" not in inbox.get(INBOX).text

    def test_a_patient_without_a_grant_contributes_nothing(
        self,
        inbox: TestClient,
        repo: InMemoryPatientMessageRepository,
        mock_audit_service: AuditService,
    ) -> None:
        _thread(repo, _STRANGER, "t-theirs", at=_T0)
        _thread(repo, _ADA, "t-ours", at=_T0)

        body = inbox.get(INBOX).json()

        assert [row["id"] for row in body["data"]] == ["t-ours"]
        assert all(entry.patient_id != _STRANGER for entry in _entries(mock_audit_service))

    def test_a_deleted_patients_threads_are_left_out(
        self, inbox: TestClient, repo: InMemoryPatientMessageRepository, mock_user_id: str
    ) -> None:
        repo.name_patient(_GRACE, "Grace Hopper", deleted=True)
        _thread(repo, _GRACE, "t-gone", at=_T0)

        assert inbox.get(INBOX).json()["data"] == []

    def test_closed_threads_have_their_own_view(
        self, inbox: TestClient, repo: InMemoryPatientMessageRepository, mock_user_id: str
    ) -> None:
        _thread(repo, _ADA, "t-open", at=_T0)
        _thread(repo, _ADA, "t-closed", at=_T0)
        repo.close_thread("t-closed", mock_user_id, _T0 + timedelta(hours=1))

        assert [r["id"] for r in inbox.get(INBOX).json()["data"]] == ["t-open"]
        closed = inbox.get(INBOX, params={"status": "closed"}).json()["data"]
        assert [r["id"] for r in closed] == ["t-closed"]
        assert len(inbox.get(INBOX, params={"status": "all"}).json()["data"]) == 2

    def test_audited_once_per_patient_never_per_thread(
        self,
        inbox: TestClient,
        repo: InMemoryPatientMessageRepository,
        mock_audit_service: AuditService,
    ) -> None:
        _thread(repo, _ADA, "t1", at=_T0)
        _thread(repo, _ADA, "t2", at=_T0 + timedelta(minutes=1))
        _thread(repo, _GRACE, "t3", at=_T0)

        inbox.get(INBOX)

        viewed = [
            e
            for e in _entries(mock_audit_service)
            if e.action == AuditAction.PATIENT_MESSAGE_THREAD_VIEWED
        ]
        assert sorted((e.resource_id, e.changes["thread_count"]) for e in viewed) == [
            (_ADA, 2),
            (_GRACE, 1),
        ]

    def test_a_page_says_when_there_is_more(
        self, inbox: TestClient, repo: InMemoryPatientMessageRepository
    ) -> None:
        for i in range(3):
            _thread(repo, _ADA, f"t{i}", at=_T0 + timedelta(minutes=i))

        body = inbox.get(INBOX, params={"limit": 2}).json()

        assert body["total"] == 2
        assert body["has_more"] is True


# ── ungrouped: every message ────────────────────────────────────────────


class TestEveryMessage:
    def test_each_client_message_is_its_own_row_newest_first(
        self, inbox: TestClient, repo: InMemoryPatientMessageRepository, mock_user_id: str
    ) -> None:
        _thread(repo, _ADA, "t1", at=_T0, subject="refill", body="first")
        _say(
            repo,
            _ADA,
            "t1",
            "m-reply",
            at=_T0 + timedelta(minutes=5),
            body="ok",
            sender=SENDER_CLINICIAN,
        )
        _say(repo, _ADA, "t1", "m-second", at=_T0 + timedelta(minutes=10), body="second")
        _thread(repo, _GRACE, "t2", at=_T0 + timedelta(minutes=7), body="grace here")

        rows = inbox.get(MESSAGES).json()["data"]

        assert [r["body"] for r in rows] == ["second", "grace here", "first"]
        assert rows[0]["thread_id"] == "t1"
        assert rows[0]["patient_name"] == "Ada Lovelace"
        assert rows[0]["thread_subject"] == "refill"
        assert rows[0]["thread_status"] == "open"
        # The practice's own reply is not new to the practice.
        assert "ok" not in [r["body"] for r in rows]

    def test_unread_is_measured_from_the_threads_read_mark(
        self, inbox: TestClient, repo: InMemoryPatientMessageRepository, mock_user_id: str
    ) -> None:
        _thread(repo, _ADA, "t1", at=_T0, body="before")
        repo.mark_thread_read_by_clinician("t1", mock_user_id, _T0 + timedelta(minutes=1))
        _say(repo, _ADA, "t1", "m-after", at=_T0 + timedelta(minutes=2), body="after")

        rows = {r["body"]: r["unread"] for r in inbox.get(MESSAGES).json()["data"]}
        assert rows == {"before": False, "after": True}

        only = inbox.get(MESSAGES, params={"unread_only": True}).json()["data"]
        assert [r["body"] for r in only] == ["after"]

    def test_a_patient_without_a_grant_contributes_nothing(
        self, inbox: TestClient, repo: InMemoryPatientMessageRepository
    ) -> None:
        _thread(repo, _STRANGER, "t-theirs", at=_T0, body="not yours")

        response = inbox.get(MESSAGES)

        assert response.json()["data"] == []
        assert "not yours" not in response.text

    def test_audited_as_opening_each_thread_whose_words_it_shows(
        self,
        inbox: TestClient,
        repo: InMemoryPatientMessageRepository,
        mock_audit_service: AuditService,
    ) -> None:
        _thread(repo, _ADA, "t1", at=_T0, body="one")
        _say(repo, _ADA, "t1", "m2", at=_T0 + timedelta(minutes=1), body="two")
        _thread(repo, _GRACE, "t2", at=_T0, body="three")

        inbox.get(MESSAGES)

        viewed = [
            e
            for e in _entries(mock_audit_service)
            if e.action == AuditAction.PATIENT_MESSAGE_THREAD_VIEWED
        ]
        assert sorted((e.resource_id, e.changes["message_count"]) for e in viewed) == [
            ("t1", 2),
            ("t2", 1),
        ]
        # Counts only: the words are in the store, never in the record.
        assert not any("one" in str(e.changes) for e in viewed)


# ── the badge ───────────────────────────────────────────────────────────


class TestUnreadCount:
    def test_counts_threads_with_something_unread(
        self, inbox: TestClient, repo: InMemoryPatientMessageRepository, mock_user_id: str
    ) -> None:
        _thread(repo, _ADA, "t1", at=_T0)
        _say(repo, _ADA, "t1", "m2", at=_T0 + timedelta(minutes=1), body="again")
        _thread(repo, _GRACE, "t2", at=_T0)
        repo.mark_thread_read_by_clinician("t2", mock_user_id, _T0 + timedelta(hours=1))
        _thread(repo, _STRANGER, "t3", at=_T0)

        assert inbox.get(UNREAD).json() == {"threads_with_unread": 1}

    def test_is_not_read_as_a_thread_id(self, inbox: TestClient) -> None:
        """Declared before ``/{thread_id}``; were it after, this would 404."""
        assert inbox.get(UNREAD).status_code == 200
        assert inbox.get(MESSAGES).status_code == 200

    def test_audited_as_a_count_about_the_clinician(
        self,
        inbox: TestClient,
        repo: InMemoryPatientMessageRepository,
        mock_audit_service: AuditService,
        mock_user_id: str,
    ) -> None:
        _thread(repo, _ADA, "t1", at=_T0)

        inbox.get(UNREAD)

        (entry,) = [
            e
            for e in _entries(mock_audit_service)
            if e.action == AuditAction.PATIENT_MESSAGE_UNREAD_COUNTED
        ]
        assert entry.resource_id == mock_user_id
        assert entry.patient_id is None
        assert entry.changes == {"threads_with_unread": 1}
