# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""HTTP-level tests for files on a secure message.

Both directions, through the real dependency chain: a patient sends what
they uploaded themselves, a clinician replies with what they uploaded
themselves, and each of them is refused the other's file. The documents
are seeded into the in-memory documents repository rather than uploaded,
because what is under test here is which ids a send will accept — the
upload path itself has its own suite.

**No refusal in this file is allowed to be vacuous.** Every one is
preceded by the same send with a valid id, on the same client, so a 422
proves the rule and not a broken fixture.

The isolation claim is made twice over, here and in the integration suite:
these tests prove the routes refuse, and
``tests_integration/database/test_patient_message_attachments_rls.py``
proves the row policies underneath them do too. Neither is the reason to
skip the other.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING

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
from app.models import DocumentCategory, PatientDocument
from app.models.audit import AuditAction
from app.rate_limit import reset_patient_message_send_limiter
from app.repositories import (
    InMemoryPatientDocumentRepository,
    InMemoryPatientMessageRepository,
)
from app.routes.patient_messages import (
    get_patient_document_repository,
    get_patient_message_repository,
)
from app.services.patient_message_hooks import get_patient_message_hook_registry

if TYPE_CHECKING:
    from app.services import AuditService
    from fastapi.testclient import TestClient

_PATIENT_A = "patient-a"
_PATIENT_B = "patient-b"
_TOKEN_A = "credential-of-patient-a"
_TOKEN_B = "credential-of-patient-b"

_BODY = "Here is the card you asked for."
PATIENT_BASE = "/api/patient/messages/threads"

_NOW = datetime(2026, 9, 20, 9, 0, tzinfo=UTC)


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
def document_repo() -> InMemoryPatientDocumentRepository:
    return InMemoryPatientDocumentRepository()


@pytest.fixture(autouse=True)
def no_hooks():
    registry = get_patient_message_hook_registry()
    registry.clear()
    yield registry
    registry.clear()


@pytest.fixture
def patient_client(
    client: TestClient,
    message_repo: InMemoryPatientMessageRepository,
    document_repo: InMemoryPatientDocumentRepository,
    monkeypatch: pytest.MonkeyPatch,
) -> TestClient:
    reset_patient_message_send_limiter()
    registry = PatientResolverRegistry()
    registry.register(_TwoPatientResolver())
    app.dependency_overrides[get_patient_resolver_registry] = lambda: registry
    app.dependency_overrides[get_patient_message_repository] = lambda: message_repo
    app.dependency_overrides[get_patient_document_repository] = lambda: document_repo
    monkeypatch.setattr(patient_context_module, "get_db_session", object)
    monkeypatch.setattr(patient_context_module, "set_tenant_schema", lambda _s, _schema: None)
    monkeypatch.setattr(patient_context_module, "arm_current_patient_id", lambda _s, _p: None)
    return client


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _give_document(
    message_repo: InMemoryPatientMessageRepository,
    document_repo: InMemoryPatientDocumentRepository,
    document_id: str,
    *,
    patient_id: str = _PATIENT_A,
    uploaded_by_patient_id: str | None = _PATIENT_A,
    user_id: str | None = None,
    category: DocumentCategory = DocumentCategory.MESSAGE,
    finalized: bool = True,
    filename: str = "insurance-card.png",
) -> PatientDocument:
    """A document on a chart, and the description the thread read joins for.

    Two repositories because the product has two tables: the document row,
    and the ``patient_documents`` columns the attachment join reads. The
    in-memory message repository has no document table, so it is told what
    the real join would have found — see ``describe_document``.
    """
    document = PatientDocument(
        id=document_id,
        patient_id=patient_id,
        user_id=user_id,
        uploaded_by_patient_id=uploaded_by_patient_id,
        filename=filename,
        mime_type="image/png",
        gcs_path=f"tenant/{document_id}.png",
        size_bytes=2048,
        created_at=_NOW,
        finalized_at=_NOW if finalized else None,
        category=category,
    )
    document_repo.add(document)
    message_repo.describe_document(
        document_id, filename=filename, mime_type="image/png", size_bytes=2048
    )
    return document


def _start(client: TestClient, token: str, **body: object) -> dict:
    response = client.post(
        PATIENT_BASE, json={"subject": None, "body": _BODY, **body}, headers=_auth(token)
    )
    assert response.status_code == 201, response.text
    return response.json()


def _entries(audit: AuditService) -> list:
    return [call.args[0] for call in audit._repo.append.call_args_list]


# ---------------------------------------------------------------------------
# A patient sends files
# ---------------------------------------------------------------------------


class TestPatientSendsAttachments:
    def test_a_send_carries_its_files_in_the_reply(
        self,
        patient_client: TestClient,
        message_repo: InMemoryPatientMessageRepository,
        document_repo: InMemoryPatientDocumentRepository,
    ) -> None:
        _give_document(message_repo, document_repo, "doc-1")

        thread = _start(patient_client, _TOKEN_A, attachment_ids=["doc-1"])

        [attachment] = thread["messages"][0]["attachments"]
        assert attachment == {
            "document_id": "doc-1",
            "filename": "insurance-card.png",
            "mime_type": "image/png",
            "size_bytes": 2048,
        }

    def test_reading_the_thread_back_lists_them_too(
        self,
        patient_client: TestClient,
        message_repo: InMemoryPatientMessageRepository,
        document_repo: InMemoryPatientDocumentRepository,
    ) -> None:
        """The send builds its payload from what it validated; this reads it."""
        _give_document(message_repo, document_repo, "doc-1")
        thread_id = _start(patient_client, _TOKEN_A, attachment_ids=["doc-1"])["id"]

        detail = patient_client.get(f"{PATIENT_BASE}/{thread_id}", headers=_auth(_TOKEN_A))

        assert [a["document_id"] for a in detail.json()["messages"][0]["attachments"]] == ["doc-1"]

    def test_five_files_are_accepted_and_six_are_not(
        self,
        patient_client: TestClient,
        message_repo: InMemoryPatientMessageRepository,
        document_repo: InMemoryPatientDocumentRepository,
    ) -> None:
        ids = [f"doc-{n}" for n in range(6)]
        for document_id in ids:
            _give_document(message_repo, document_repo, document_id)

        thread = _start(patient_client, _TOKEN_A, attachment_ids=ids[:5])
        assert len(thread["messages"][0]["attachments"]) == 5

        too_many = patient_client.post(
            PATIENT_BASE, json={"body": _BODY, "attachment_ids": ids}, headers=_auth(_TOKEN_A)
        )
        assert too_many.status_code == 422

    def test_a_message_with_no_files_is_an_ordinary_message(
        self, patient_client: TestClient
    ) -> None:
        """The field is optional, so nothing about sending text changes."""
        thread = _start(patient_client, _TOKEN_A)
        assert thread["messages"][0]["attachments"] == []

    def test_sending_into_an_existing_thread_carries_files_too(
        self,
        patient_client: TestClient,
        message_repo: InMemoryPatientMessageRepository,
        document_repo: InMemoryPatientDocumentRepository,
    ) -> None:
        thread_id = _start(patient_client, _TOKEN_A)["id"]
        _give_document(message_repo, document_repo, "doc-1")

        response = patient_client.post(
            f"{PATIENT_BASE}/{thread_id}/messages",
            json={"body": "and the letter", "attachment_ids": ["doc-1"]},
            headers=_auth(_TOKEN_A),
        )

        assert response.status_code == 201, response.text
        assert [a["document_id"] for a in response.json()["attachments"]] == ["doc-1"]


# ---------------------------------------------------------------------------
# What a send refuses
# ---------------------------------------------------------------------------


class TestPatientAttachmentRules:
    """Each refusal, with the accepting control immediately before it."""

    def _control(
        self,
        patient_client: TestClient,
        message_repo: InMemoryPatientMessageRepository,
        document_repo: InMemoryPatientDocumentRepository,
    ) -> None:
        _give_document(message_repo, document_repo, "doc-ok")
        _start(patient_client, _TOKEN_A, attachment_ids=["doc-ok"])

    def _refused(self, patient_client: TestClient, document_id: str) -> dict:
        response = patient_client.post(
            PATIENT_BASE,
            json={"body": _BODY, "attachment_ids": [document_id]},
            headers=_auth(_TOKEN_A),
        )
        assert response.status_code == 422, response.text
        return response.json()["error"]

    def test_an_id_that_names_nothing(
        self,
        patient_client: TestClient,
        message_repo: InMemoryPatientMessageRepository,
        document_repo: InMemoryPatientDocumentRepository,
    ) -> None:
        self._control(patient_client, message_repo, document_repo)
        error = self._refused(patient_client, "doc-nobody-has")
        assert error["code"] == "ATTACHMENT_NOT_AVAILABLE"

    def test_an_upload_that_never_finished(
        self,
        patient_client: TestClient,
        message_repo: InMemoryPatientMessageRepository,
        document_repo: InMemoryPatientDocumentRepository,
    ) -> None:
        self._control(patient_client, message_repo, document_repo)
        _give_document(message_repo, document_repo, "doc-half", finalized=False)
        assert self._refused(patient_client, "doc-half")["code"] == "ATTACHMENT_NOT_AVAILABLE"

    def test_a_document_filed_under_another_category(
        self,
        patient_client: TestClient,
        message_repo: InMemoryPatientMessageRepository,
        document_repo: InMemoryPatientDocumentRepository,
    ) -> None:
        """An insurance card sent in before a first visit is not correspondence."""
        self._control(patient_client, message_repo, document_repo)
        _give_document(
            message_repo,
            document_repo,
            "doc-intake",
            category=DocumentCategory.INTAKE_ARTIFACT,
        )
        assert self._refused(patient_client, "doc-intake")["code"] == "ATTACHMENT_NOT_AVAILABLE"

    def test_another_patients_document(
        self,
        patient_client: TestClient,
        message_repo: InMemoryPatientMessageRepository,
        document_repo: InMemoryPatientDocumentRepository,
    ) -> None:
        """Same answer as an id that names nothing — no existence oracle."""
        self._control(patient_client, message_repo, document_repo)
        _give_document(
            message_repo,
            document_repo,
            "doc-of-b",
            patient_id=_PATIENT_B,
            uploaded_by_patient_id=_PATIENT_B,
        )

        error = self._refused(patient_client, "doc-of-b")

        assert error["code"] == "ATTACHMENT_NOT_AVAILABLE"
        assert "doc-of-b" not in str(error)

    def test_a_document_a_clinician_put_on_the_patients_own_chart(
        self,
        patient_client: TestClient,
        message_repo: InMemoryPatientMessageRepository,
        document_repo: InMemoryPatientDocumentRepository,
    ) -> None:
        """Readable by the patient, and still not theirs to forward."""
        self._control(patient_client, message_repo, document_repo)
        _give_document(
            message_repo,
            document_repo,
            "doc-from-clinician",
            uploaded_by_patient_id=None,
            user_id="u-1",
        )
        assert (
            self._refused(patient_client, "doc-from-clinician")["code"]
            == "ATTACHMENT_NOT_AVAILABLE"
        )

    def test_a_file_already_sent_on_another_message(
        self,
        patient_client: TestClient,
        message_repo: InMemoryPatientMessageRepository,
        document_repo: InMemoryPatientDocumentRepository,
    ) -> None:
        _give_document(message_repo, document_repo, "doc-1")
        _start(patient_client, _TOKEN_A, attachment_ids=["doc-1"])

        error = self._refused(patient_client, "doc-1")

        assert error["code"] == "ATTACHMENT_ALREADY_SENT"

    def test_the_same_id_twice_in_one_send(
        self,
        patient_client: TestClient,
        message_repo: InMemoryPatientMessageRepository,
        document_repo: InMemoryPatientDocumentRepository,
    ) -> None:
        """The same rule, caught before the write rather than by the constraint."""
        _give_document(message_repo, document_repo, "doc-1")

        response = patient_client.post(
            PATIENT_BASE,
            json={"body": _BODY, "attachment_ids": ["doc-1", "doc-1"]},
            headers=_auth(_TOKEN_A),
        )

        assert response.status_code == 422
        assert response.json()["error"]["code"] == "ATTACHMENT_ALREADY_SENT"

    def test_a_refused_send_writes_no_message(
        self,
        patient_client: TestClient,
        message_repo: InMemoryPatientMessageRepository,
        document_repo: InMemoryPatientDocumentRepository,
    ) -> None:
        """Validation runs before the insert, so a bad id sends nothing."""
        self._control(patient_client, message_repo, document_repo)
        before = patient_client.get(PATIENT_BASE, headers=_auth(_TOKEN_A)).json()["total"]

        self._refused(patient_client, "doc-nobody-has")

        assert patient_client.get(PATIENT_BASE, headers=_auth(_TOKEN_A)).json()["total"] == before


# ---------------------------------------------------------------------------
# A clinician replies with files
# ---------------------------------------------------------------------------


class TestClinicianRepliesWithAttachments:
    def test_a_reply_carries_the_clinicians_own_upload(
        self,
        patient_client: TestClient,
        message_repo: InMemoryPatientMessageRepository,
        document_repo: InMemoryPatientDocumentRepository,
        mock_user_id: str,
    ) -> None:
        thread_id = _start(patient_client, _TOKEN_A)["id"]
        message_repo.grant_access(_PATIENT_A, mock_user_id)
        document_repo.grant_access(_PATIENT_A, mock_user_id)
        _give_document(
            message_repo,
            document_repo,
            "doc-from-clinician",
            uploaded_by_patient_id=None,
            user_id=mock_user_id,
            filename="after-visit-summary.pdf",
        )

        reply = patient_client.post(
            f"/api/message-threads/{thread_id}/replies",
            json={"body": "Here it is.", "attachment_ids": ["doc-from-clinician"]},
        )

        assert reply.status_code == 201, reply.text
        assert [a["document_id"] for a in reply.json()["attachments"]] == ["doc-from-clinician"]

    def test_the_patient_sees_what_the_clinician_sent(
        self,
        patient_client: TestClient,
        message_repo: InMemoryPatientMessageRepository,
        document_repo: InMemoryPatientDocumentRepository,
        mock_user_id: str,
    ) -> None:
        """The loop closes: a reply's file reaches the patient's own thread."""
        thread_id = _start(patient_client, _TOKEN_A)["id"]
        message_repo.grant_access(_PATIENT_A, mock_user_id)
        document_repo.grant_access(_PATIENT_A, mock_user_id)
        _give_document(
            message_repo,
            document_repo,
            "doc-from-clinician",
            uploaded_by_patient_id=None,
            user_id=mock_user_id,
        )
        patient_client.post(
            f"/api/message-threads/{thread_id}/replies",
            json={"body": "Here it is.", "attachment_ids": ["doc-from-clinician"]},
        )

        detail = patient_client.get(f"{PATIENT_BASE}/{thread_id}", headers=_auth(_TOKEN_A))

        messages = detail.json()["messages"]
        assert [a["document_id"] for a in messages[-1]["attachments"]] == ["doc-from-clinician"]

    def test_a_clinician_cannot_send_the_patients_own_upload(
        self,
        patient_client: TestClient,
        message_repo: InMemoryPatientMessageRepository,
        document_repo: InMemoryPatientDocumentRepository,
        mock_user_id: str,
    ) -> None:
        """Reachable on the chart, and still not the replier's to send."""
        thread_id = _start(patient_client, _TOKEN_A)["id"]
        message_repo.grant_access(_PATIENT_A, mock_user_id)
        document_repo.grant_access(_PATIENT_A, mock_user_id)
        # Control: the clinician's own upload goes through on this thread.
        _give_document(
            message_repo,
            document_repo,
            "doc-mine",
            uploaded_by_patient_id=None,
            user_id=mock_user_id,
        )
        accepted = patient_client.post(
            f"/api/message-threads/{thread_id}/replies",
            json={"body": "mine", "attachment_ids": ["doc-mine"]},
        )
        assert accepted.status_code == 201, accepted.text

        _give_document(message_repo, document_repo, "doc-of-patient")
        refused = patient_client.post(
            f"/api/message-threads/{thread_id}/replies",
            json={"body": "theirs", "attachment_ids": ["doc-of-patient"]},
        )

        assert refused.status_code == 422
        assert refused.json()["error"]["code"] == "ATTACHMENT_NOT_AVAILABLE"

    def test_a_clinician_cannot_send_a_document_from_another_chart(
        self,
        patient_client: TestClient,
        message_repo: InMemoryPatientMessageRepository,
        document_repo: InMemoryPatientDocumentRepository,
        mock_user_id: str,
    ) -> None:
        """Uploaded by this clinician, for a different patient."""
        thread_id = _start(patient_client, _TOKEN_A)["id"]
        message_repo.grant_access(_PATIENT_A, mock_user_id)
        document_repo.grant_access(_PATIENT_A, mock_user_id)
        document_repo.grant_access(_PATIENT_B, mock_user_id)
        _give_document(
            message_repo,
            document_repo,
            "doc-mine",
            uploaded_by_patient_id=None,
            user_id=mock_user_id,
        )
        assert (
            patient_client.post(
                f"/api/message-threads/{thread_id}/replies",
                json={"body": "mine", "attachment_ids": ["doc-mine"]},
            ).status_code
            == 201
        )

        _give_document(
            message_repo,
            document_repo,
            "doc-other-chart",
            patient_id=_PATIENT_B,
            uploaded_by_patient_id=None,
            user_id=mock_user_id,
        )
        refused = patient_client.post(
            f"/api/message-threads/{thread_id}/replies",
            json={"body": "wrong chart", "attachment_ids": ["doc-other-chart"]},
        )

        assert refused.status_code == 422
        assert refused.json()["error"]["code"] == "ATTACHMENT_NOT_AVAILABLE"


# ---------------------------------------------------------------------------
# Audit and logs
# ---------------------------------------------------------------------------


class TestAttachmentAudit:
    def test_a_send_records_how_many_files_went_with_it(
        self,
        patient_client: TestClient,
        message_repo: InMemoryPatientMessageRepository,
        document_repo: InMemoryPatientDocumentRepository,
        mock_audit_service: AuditService,
    ) -> None:
        _give_document(message_repo, document_repo, "doc-1")
        _give_document(message_repo, document_repo, "doc-2", filename="letter.png")

        _start(patient_client, _TOKEN_A, attachment_ids=["doc-1", "doc-2"])

        sent = [
            e
            for e in _entries(mock_audit_service)
            if e.action == AuditAction.PATIENT_MESSAGE_SENT.value
        ]
        assert sent[-1].changes["attachment_count"] == 2

    def test_no_filename_reaches_an_audit_payload_or_a_log_line(
        self,
        patient_client: TestClient,
        message_repo: InMemoryPatientMessageRepository,
        document_repo: InMemoryPatientDocumentRepository,
        mock_audit_service: AuditService,
        mock_user_id: str,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """People name files after what is in them. Guardrail #5 covers that."""
        telling_filename = "biopsy-result-2026.png"
        _give_document(message_repo, document_repo, "doc-1", filename=telling_filename)

        with caplog.at_level(logging.DEBUG):
            thread_id = _start(patient_client, _TOKEN_A, attachment_ids=["doc-1"])["id"]
            message_repo.grant_access(_PATIENT_A, mock_user_id)
            patient_client.get(f"/api/message-threads/{thread_id}")

        rendered = "\n".join(str(e.changes) for e in _entries(mock_audit_service))
        logged = "\n".join(record.getMessage() for record in caplog.records)
        assert telling_filename not in rendered
        assert telling_filename not in logged

    def test_a_clinician_opening_a_thread_records_the_count(
        self,
        patient_client: TestClient,
        message_repo: InMemoryPatientMessageRepository,
        document_repo: InMemoryPatientDocumentRepository,
        mock_audit_service: AuditService,
        mock_user_id: str,
    ) -> None:
        """What was disclosed, as a number rather than a list of names."""
        _give_document(message_repo, document_repo, "doc-1")
        thread_id = _start(patient_client, _TOKEN_A, attachment_ids=["doc-1"])["id"]
        message_repo.grant_access(_PATIENT_A, mock_user_id)

        patient_client.get(f"/api/message-threads/{thread_id}")

        viewed = [
            e
            for e in _entries(mock_audit_service)
            if e.action == AuditAction.PATIENT_MESSAGE_THREAD_VIEWED.value
        ]
        assert viewed[-1].changes["attachment_count"] == 1


# ---------------------------------------------------------------------------
# The notification seam is unchanged
# ---------------------------------------------------------------------------


def test_a_send_with_files_dispatches_the_same_event_shape(
    patient_client: TestClient,
    message_repo: InMemoryPatientMessageRepository,
    document_repo: InMemoryPatientDocumentRepository,
    no_hooks,
) -> None:
    """Notifications carry a link. Attachments add nothing for them to leak."""
    telling_filename = "custody-order.png"
    _give_document(message_repo, document_repo, "doc-1", filename=telling_filename)
    seen: list = []
    no_hooks.register(seen.append)

    _start(patient_client, _TOKEN_A, attachment_ids=["doc-1"])

    assert len(seen) == 1
    assert telling_filename not in str(vars(seen[0]))
    assert not hasattr(seen[0], "attachments")
