# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""HTTP-level tests for attaching a file to a form from the portal.

The validator tests prove what a finished answer looks like. These prove
the door: which principal the route trusts, which status code each refusal
gets, what the item's answer becomes afterwards, and what the audit entry
says about it.

The patient front door is the real ``get_patient_context`` with a
synthesized resolver and the session arming patched out, exactly as the
assignment and signature tests do it — so what is under test is what the
ROUTE does with a principal. Two-patient isolation at the database layer
belongs to ``tests_integration/database/test_patient_intake_artifacts_rls.py``;
what is provable here is the half a row policy cannot fix, which is that
no field of the request names a patient at all.

One property is worth naming because the whole design rests on it: the
item's answer is never taken from the request. It is written from the
artifact rows, which were written from documents the service had already
checked belong to the caller. The test that matters most here is therefore
:meth:`TestAnotherPatientsFile.test_a_cannot_attach_bs_document` — A knows
B's document id and still cannot put it on A's form.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest
from app.api_errors import register_exception_handlers
from app.auth import patient_context as patient_context_module
from app.auth.patient_context import (
    AuthStrength,
    PatientContext,
    PatientCredential,
    PatientResolverRegistry,
    get_patient_resolver_registry,
)
from app.intake.items import ItemDraft
from app.main import app as real_app
from app.models import DocumentCategory, Patient, PatientDocument
from app.models.audit import ACTOR_TYPE_PATIENT, AuditAction
from app.repositories import (
    InMemoryIntakePacketRepository,
    InMemoryPatientDocumentRepository,
    InMemoryPatientIntakeArtifactRepository,
    InMemoryPatientIntakeAssignmentRepository,
    InMemoryPatientRepository,
    get_patient_repository,
)
from app.repositories.audit import InMemoryAuditRepository
from app.repositories.coverage import (
    InMemoryPatientCoverageRepository,
    InMemoryPayerRepository,
)
from app.routes import patient_intake_assignments
from app.routes.patient_intake_assignments import (
    get_clinician_intake_artifact_service,
    get_clinician_intake_assignment_service,
    get_clinician_patient_document_repository,
    get_clinician_patient_repository,
    get_patient_intake_artifact_service,
    get_patient_intake_assignment_service,
)
from app.services.audit_service import AuditService, get_audit_service
from app.services.intake_packet_service import IntakePacketService
from app.services.patient_intake_artifact_service import IntakeArtifactService
from app.services.patient_intake_assignment_service import IntakeAssignmentService
from app.settings import get_settings
from fastapi import FastAPI
from fastapi.testclient import TestClient

if TYPE_CHECKING:
    from collections.abc import Iterator

_PATIENT_A = "11111111-1111-4111-8111-111111111111"
_PATIENT_B = "22222222-2222-4222-8222-222222222222"
_TOKEN_A = "credential-of-patient-a"
_TOKEN_B = "credential-of-patient-b"
_TOKEN_A_WEAK = "single-factor-credential-of-patient-a"
_SESSION_A = "session-handle-a"
_CLINICIAN = "clinician-1"

ASSIGNMENTS = "/api/patient/intake/assignments"


class _TwoPatientResolver:
    credential_kind = "bearer"

    def resolve(self, credential: PatientCredential) -> PatientContext | None:
        principals = {
            _TOKEN_A: (_PATIENT_A, AuthStrength.STEPPED_UP, _SESSION_A),
            _TOKEN_B: (_PATIENT_B, AuthStrength.STEPPED_UP, "session-handle-b"),
            _TOKEN_A_WEAK: (_PATIENT_A, AuthStrength.SINGLE_FACTOR, "session-handle-weak"),
        }
        found = principals.get(credential.value)
        if found is None:
            return None
        patient_id, strength, session_id = found
        return PatientContext(
            patient_id=patient_id,
            practice_schema="practice_test_intake",
            credential_kind="bearer",
            auth_strength=strength,
            session_id=session_id,
        )


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def packets() -> InMemoryIntakePacketRepository:
    return InMemoryIntakePacketRepository()


@pytest.fixture
def packet_service(packets: InMemoryIntakePacketRepository) -> IntakePacketService:
    return IntakePacketService(packets)


@pytest.fixture
def assignments() -> InMemoryPatientIntakeAssignmentRepository:
    repo = InMemoryPatientIntakeAssignmentRepository()
    repo.grant_access(_PATIENT_A, _CLINICIAN)
    repo.grant_access(_PATIENT_B, _CLINICIAN)
    return repo


@pytest.fixture
def documents() -> InMemoryPatientDocumentRepository:
    repo = InMemoryPatientDocumentRepository()
    repo.grant_access(_PATIENT_A, _CLINICIAN)
    repo.grant_access(_PATIENT_B, _CLINICIAN)
    return repo


@pytest.fixture
def artifacts() -> InMemoryPatientIntakeArtifactRepository:
    repo = InMemoryPatientIntakeArtifactRepository()
    repo.grant_access(_PATIENT_A, _CLINICIAN)
    repo.grant_access(_PATIENT_B, _CLINICIAN)
    return repo


@pytest.fixture
def coverage() -> InMemoryPatientCoverageRepository:
    return InMemoryPatientCoverageRepository()


@pytest.fixture
def assignment_service(
    assignments: InMemoryPatientIntakeAssignmentRepository,
    packets: InMemoryIntakePacketRepository,
) -> IntakeAssignmentService:
    return IntakeAssignmentService(assignments, packets)


@pytest.fixture
def artifact_service(
    artifacts: InMemoryPatientIntakeArtifactRepository,
    assignments: InMemoryPatientIntakeAssignmentRepository,
    packets: InMemoryIntakePacketRepository,
    documents: InMemoryPatientDocumentRepository,
    coverage: InMemoryPatientCoverageRepository,
) -> IntakeArtifactService:
    return IntakeArtifactService(
        artifacts,
        assignments,
        packets,
        documents,
        InMemoryPayerRepository(),
        coverage,
    )


@pytest.fixture
def audit_repo() -> InMemoryAuditRepository:
    return InMemoryAuditRepository()


@pytest.fixture
def patient_app(
    assignment_service: IntakeAssignmentService,
    artifact_service: IntakeArtifactService,
    audit_repo: InMemoryAuditRepository,
    monkeypatch: pytest.MonkeyPatch,
) -> FastAPI:
    """The patient router alone, with a patient front door and no database."""
    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(patient_intake_assignments.router)

    registry = PatientResolverRegistry()
    registry.register(_TwoPatientResolver())
    app.dependency_overrides[get_patient_resolver_registry] = lambda: registry
    app.dependency_overrides[get_patient_intake_assignment_service] = lambda: assignment_service
    app.dependency_overrides[get_patient_intake_artifact_service] = lambda: artifact_service
    app.dependency_overrides[get_audit_service] = lambda: AuditService(audit_repo)

    monkeypatch.setattr(patient_context_module, "get_db_session", object)
    monkeypatch.setattr(patient_context_module, "set_tenant_schema", lambda _s, _schema: None)
    monkeypatch.setattr(patient_context_module, "arm_current_patient_id", lambda _s, _p: None)
    return app


@pytest.fixture
def portal(patient_app: FastAPI) -> Iterator[TestClient]:
    with TestClient(patient_app) as c:
        yield c


@pytest.fixture
def form(packet_service: IntakePacketService) -> str:
    """A published form with a card (both sides) and a document request."""
    template = packet_service.create_template("Intake", _CLINICIAN)
    version_id = str(packet_service.list_versions(str(template["id"]))[0]["id"])
    packet_service.replace_items(
        version_id,
        [
            ItemDraft(
                key="card",
                item_type="insurance_card",
                label="A photo of your insurance card",
                config={"sides": "both"},
            ),
            ItemDraft(
                key="records",
                item_type="document_request",
                label="Any records from a previous provider",
                config={},
            ),
            ItemDraft(
                key="reason",
                item_type="reason",
                config={},
            ),
        ],
    )
    packet_service.publish(version_id, _CLINICIAN)
    return version_id


@pytest.fixture
def assignment(assignment_service: IntakeAssignmentService, form: str) -> dict[str, object]:
    row, _ = assignment_service.assign(_PATIENT_A, form, _CLINICIAN)
    return row


def _item(assignment_service: IntakeAssignmentService, form: str, key: str) -> str:
    return next(str(row["id"]) for row in assignment_service.items(form) if row["key"] == key)


@pytest.fixture
def card_item(assignment_service: IntakeAssignmentService, form: str) -> str:
    return _item(assignment_service, form, "card")


@pytest.fixture
def records_item(assignment_service: IntakeAssignmentService, form: str) -> str:
    return _item(assignment_service, form, "records")


@pytest.fixture
def reason_item(assignment_service: IntakeAssignmentService, form: str) -> str:
    return _item(assignment_service, form, "reason")


def _upload(
    documents: InMemoryPatientDocumentRepository,
    patient_id: str,
    *,
    document_id: str,
    category: DocumentCategory = DocumentCategory.INTAKE_ARTIFACT,
    finalized: bool = True,
) -> str:
    """A document as the patient's own upload path leaves one behind."""
    now = datetime.now(UTC)
    documents.add(
        PatientDocument(
            id=document_id,
            patient_id=patient_id,
            user_id=None,
            uploaded_by_patient_id=patient_id,
            filename="card.png",
            mime_type="image/png",
            gcs_path=f"tenant/{category.value}/{document_id}",
            size_bytes=1234,
            category=category,
            created_at=now,
            finalized_at=now if finalized else None,
        )
    )
    return document_id


_DOC_FRONT = "aaaaaaaa-0000-4000-8000-000000000001"
_DOC_BACK = "aaaaaaaa-0000-4000-8000-000000000002"
_DOC_RECORD = "aaaaaaaa-0000-4000-8000-000000000003"
_DOC_B = "bbbbbbbb-0000-4000-8000-000000000001"


def _artifacts_url(assignment_id: str) -> str:
    return f"{ASSIGNMENTS}/{assignment_id}/artifacts"


def _only(audit_repo: InMemoryAuditRepository, action: AuditAction):  # type: ignore[no-untyped-def]
    """The one entry for *action*, whatever order the store hands them back.

    Selected by action rather than by position: a positional read would
    pass or fail on the repository's sort order rather than on what was
    recorded, which is the thing under test.
    """
    found = [
        entry for entry in audit_repo.list_for_user(_PATIENT_A) if entry.action == action.value
    ]
    assert len(found) == 1, f"expected one {action.value} entry, got {len(found)}"
    return found[0]


def _attach(
    portal: TestClient,
    assignment_id: str,
    item_id: str,
    document_id: str,
    *,
    side: str | None = None,
    token: str = _TOKEN_A,
):  # type: ignore[no-untyped-def]
    body: dict[str, object] = {"item_id": item_id, "document_id": document_id}
    if side is not None:
        body["side"] = side
    return portal.post(_artifacts_url(assignment_id), json=body, headers=_auth(token))


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestAttaching:
    def test_a_card_side_is_attached_and_becomes_the_answer(
        self,
        portal: TestClient,
        documents: InMemoryPatientDocumentRepository,
        assignment: dict[str, object],
        card_item: str,
    ) -> None:
        """The control for everything below, and the shape of the answer.

        The value the form holds afterwards was not sent by the caller: it
        was computed from the artifact row the route had just written.
        """
        _upload(documents, _PATIENT_A, document_id=_DOC_FRONT)
        response = _attach(portal, str(assignment["id"]), card_item, _DOC_FRONT, side="front")

        assert response.status_code == 201
        body = response.json()
        assert body["artifact"]["document_id"] == _DOC_FRONT
        assert body["artifact"]["side"] == "front"
        # One side of two, so the form is not finished and the server says so.
        assert body["progress"]["complete"] is False
        assert card_item in body["progress"]["missing"]
        # The first write moves the form off "sent".
        assert body["status"] == "in_progress"

    def test_both_sides_settle_the_card(
        self,
        portal: TestClient,
        documents: InMemoryPatientDocumentRepository,
        assignment: dict[str, object],
        card_item: str,
    ) -> None:
        _upload(documents, _PATIENT_A, document_id=_DOC_FRONT)
        _upload(documents, _PATIENT_A, document_id=_DOC_BACK)
        _attach(portal, str(assignment["id"]), card_item, _DOC_FRONT, side="front")
        response = _attach(portal, str(assignment["id"]), card_item, _DOC_BACK, side="back")

        assert response.status_code == 201
        assert card_item not in response.json()["progress"]["missing"]

    def test_a_document_request_takes_a_file_with_no_side(
        self,
        portal: TestClient,
        documents: InMemoryPatientDocumentRepository,
        assignment: dict[str, object],
        records_item: str,
    ) -> None:
        _upload(documents, _PATIENT_A, document_id=_DOC_RECORD)
        response = _attach(portal, str(assignment["id"]), records_item, _DOC_RECORD)

        assert response.status_code == 201
        assert response.json()["artifact"]["side"] is None
        assert records_item not in response.json()["progress"]["missing"]

    def test_the_form_detail_carries_what_is_attached(
        self,
        portal: TestClient,
        documents: InMemoryPatientDocumentRepository,
        assignment: dict[str, object],
        card_item: str,
    ) -> None:
        """One read tells the portal which photographs have arrived."""
        _upload(documents, _PATIENT_A, document_id=_DOC_FRONT)
        _attach(portal, str(assignment["id"]), card_item, _DOC_FRONT, side="front")

        detail = portal.get(f"{ASSIGNMENTS}/{assignment['id']}", headers=_auth(_TOKEN_A)).json()
        assert [row["document_id"] for row in detail["artifacts"]] == [_DOC_FRONT]
        answer = next(item for item in detail["items"] if item["id"] == card_item)["value"]
        assert answer == {"documents": [{"document_id": _DOC_FRONT, "side": "front"}]}


class TestRefusals:
    def test_a_single_factor_session_is_403(
        self,
        portal: TestClient,
        documents: InMemoryPatientDocumentRepository,
        assignment: dict[str, object],
        card_item: str,
    ) -> None:
        _upload(documents, _PATIENT_A, document_id=_DOC_FRONT)
        response = _attach(
            portal,
            str(assignment["id"]),
            card_item,
            _DOC_FRONT,
            side="front",
            token=_TOKEN_A_WEAK,
        )
        assert response.status_code == 403

    def test_a_question_that_does_not_ask_for_a_file_is_422(
        self,
        portal: TestClient,
        documents: InMemoryPatientDocumentRepository,
        assignment: dict[str, object],
        reason_item: str,
    ) -> None:
        _upload(documents, _PATIENT_A, document_id=_DOC_RECORD)
        response = _attach(portal, str(assignment["id"]), reason_item, _DOC_RECORD)
        assert response.status_code == 422

    def test_a_side_the_question_did_not_ask_for_is_422(
        self,
        portal: TestClient,
        documents: InMemoryPatientDocumentRepository,
        assignment: dict[str, object],
        records_item: str,
    ) -> None:
        """A document request has no sides, so naming one is a refusal."""
        _upload(documents, _PATIENT_A, document_id=_DOC_RECORD)
        response = _attach(portal, str(assignment["id"]), records_item, _DOC_RECORD, side="front")
        assert response.status_code == 422

    def test_a_card_with_no_side_is_422(
        self,
        portal: TestClient,
        documents: InMemoryPatientDocumentRepository,
        assignment: dict[str, object],
        card_item: str,
    ) -> None:
        _upload(documents, _PATIENT_A, document_id=_DOC_FRONT)
        response = _attach(portal, str(assignment["id"]), card_item, _DOC_FRONT)
        assert response.status_code == 422

    def test_the_same_side_twice_is_409(
        self,
        portal: TestClient,
        documents: InMemoryPatientDocumentRepository,
        assignment: dict[str, object],
        card_item: str,
    ) -> None:
        _upload(documents, _PATIENT_A, document_id=_DOC_FRONT)
        _upload(documents, _PATIENT_A, document_id=_DOC_BACK)
        _attach(portal, str(assignment["id"]), card_item, _DOC_FRONT, side="front")
        response = _attach(portal, str(assignment["id"]), card_item, _DOC_BACK, side="front")
        assert response.status_code == 409

    def test_one_file_answers_one_question(
        self,
        portal: TestClient,
        documents: InMemoryPatientDocumentRepository,
        assignment: dict[str, object],
        card_item: str,
    ) -> None:
        """The same photograph cannot be both sides of the card."""
        _upload(documents, _PATIENT_A, document_id=_DOC_FRONT)
        _attach(portal, str(assignment["id"]), card_item, _DOC_FRONT, side="front")
        response = _attach(portal, str(assignment["id"]), card_item, _DOC_FRONT, side="back")
        assert response.status_code == 409

    def test_an_unfinished_upload_is_404(
        self,
        portal: TestClient,
        documents: InMemoryPatientDocumentRepository,
        assignment: dict[str, object],
        card_item: str,
    ) -> None:
        """A row that exists and has no object behind it is not a file yet."""
        _upload(documents, _PATIENT_A, document_id=_DOC_FRONT, finalized=False)
        response = _attach(portal, str(assignment["id"]), card_item, _DOC_FRONT, side="front")
        assert response.status_code == 404

    def test_a_message_attachment_is_404(
        self,
        portal: TestClient,
        documents: InMemoryPatientDocumentRepository,
        assignment: dict[str, object],
        card_item: str,
    ) -> None:
        """The patient's other upload category is not a form's to take.

        A file sent with a secure message was sent to a person, not to a
        question, and nothing on this surface reclassifies it.
        """
        _upload(documents, _PATIENT_A, document_id=_DOC_FRONT, category=DocumentCategory.MESSAGE)
        response = _attach(portal, str(assignment["id"]), card_item, _DOC_FRONT, side="front")
        assert response.status_code == 404


class TestAnotherPatientsFile:
    def test_a_cannot_attach_bs_document(
        self,
        portal: TestClient,
        documents: InMemoryPatientDocumentRepository,
        assignment: dict[str, object],
        card_item: str,
    ) -> None:
        """The one that the whole design exists for.

        A knows B's document id — it is a uuid somebody could have seen —
        and naming it on A's own form is the same 404 an id that never
        existed gets. Nothing about B is confirmed or denied.
        """
        _upload(documents, _PATIENT_B, document_id=_DOC_B)
        response = _attach(portal, str(assignment["id"]), card_item, _DOC_B, side="front")
        assert response.status_code == 404

    def test_b_cannot_touch_as_form(
        self,
        portal: TestClient,
        documents: InMemoryPatientDocumentRepository,
        assignment: dict[str, object],
        card_item: str,
    ) -> None:
        _upload(documents, _PATIENT_B, document_id=_DOC_B)
        response = _attach(
            portal, str(assignment["id"]), card_item, _DOC_B, side="front", token=_TOKEN_B
        )
        assert response.status_code == 404


class TestRemoving:
    def test_removing_takes_the_document_with_it(
        self,
        portal: TestClient,
        documents: InMemoryPatientDocumentRepository,
        assignment: dict[str, object],
        card_item: str,
    ) -> None:
        """Remove and retake, before the form is in.

        The file was attached to a form still being filled in, so it was
        never sent to the practice — the row goes and the document is
        tombstoned with it.
        """
        _upload(documents, _PATIENT_A, document_id=_DOC_FRONT)
        attached = _attach(
            portal, str(assignment["id"]), card_item, _DOC_FRONT, side="front"
        ).json()["artifact"]

        response = portal.delete(
            f"{_artifacts_url(str(assignment['id']))}/{attached['id']}",
            headers=_auth(_TOKEN_A),
        )

        assert response.status_code == 200
        assert card_item in response.json()["progress"]["missing"]
        assert documents.get_for_patient_principal(_DOC_FRONT, _PATIENT_A) is None
        detail = portal.get(f"{ASSIGNMENTS}/{assignment['id']}", headers=_auth(_TOKEN_A)).json()
        assert detail["artifacts"] == []
        answer = next(item for item in detail["items"] if item["id"] == card_item)["value"]
        assert answer == {"documents": []}

    def test_the_slot_is_free_again_afterwards(
        self,
        portal: TestClient,
        documents: InMemoryPatientDocumentRepository,
        assignment: dict[str, object],
        card_item: str,
    ) -> None:
        """Retaking a bad photograph is the point of the delete."""
        _upload(documents, _PATIENT_A, document_id=_DOC_FRONT)
        _upload(documents, _PATIENT_A, document_id=_DOC_BACK)
        attached = _attach(
            portal, str(assignment["id"]), card_item, _DOC_FRONT, side="front"
        ).json()["artifact"]
        portal.delete(
            f"{_artifacts_url(str(assignment['id']))}/{attached['id']}",
            headers=_auth(_TOKEN_A),
        )

        again = _attach(portal, str(assignment["id"]), card_item, _DOC_BACK, side="front")
        assert again.status_code == 201

    def test_b_cannot_remove_as_artifact(
        self,
        portal: TestClient,
        documents: InMemoryPatientDocumentRepository,
        assignment: dict[str, object],
        card_item: str,
    ) -> None:
        _upload(documents, _PATIENT_A, document_id=_DOC_FRONT)
        attached = _attach(
            portal, str(assignment["id"]), card_item, _DOC_FRONT, side="front"
        ).json()["artifact"]

        response = portal.delete(
            f"{_artifacts_url(str(assignment['id']))}/{attached['id']}",
            headers=_auth(_TOKEN_B),
        )
        assert response.status_code == 404

    def test_removing_after_the_form_is_in_is_409(
        self,
        portal: TestClient,
        documents: InMemoryPatientDocumentRepository,
        assignments: InMemoryPatientIntakeAssignmentRepository,
        assignment: dict[str, object],
        card_item: str,
    ) -> None:
        """What was submitted stays submitted.

        The status is moved directly rather than through a submit, because
        what is under test is the refusal rather than the form's other
        questions.
        """
        _upload(documents, _PATIENT_A, document_id=_DOC_FRONT)
        attached = _attach(
            portal, str(assignment["id"]), card_item, _DOC_FRONT, side="front"
        ).json()["artifact"]
        assignments.assignments[str(assignment["id"])]["status"] = "submitted"

        response = portal.delete(
            f"{_artifacts_url(str(assignment['id']))}/{attached['id']}",
            headers=_auth(_TOKEN_A),
        )
        assert response.status_code == 409
        assert documents.get_for_patient_principal(_DOC_FRONT, _PATIENT_A) is not None

    def test_attaching_after_the_form_is_in_is_409(
        self,
        portal: TestClient,
        documents: InMemoryPatientDocumentRepository,
        assignments: InMemoryPatientIntakeAssignmentRepository,
        assignment: dict[str, object],
        card_item: str,
    ) -> None:
        _upload(documents, _PATIENT_A, document_id=_DOC_FRONT)
        assignments.assignments[str(assignment["id"])]["status"] = "submitted"
        response = _attach(portal, str(assignment["id"]), card_item, _DOC_FRONT, side="front")
        assert response.status_code == 409


class TestAudit:
    def test_attaching_is_recorded_without_a_filename(
        self,
        portal: TestClient,
        documents: InMemoryPatientDocumentRepository,
        audit_repo: InMemoryAuditRepository,
        assignment: dict[str, object],
        card_item: str,
    ) -> None:
        """The patient is the actor, and the payload names ids only."""
        _upload(documents, _PATIENT_A, document_id=_DOC_FRONT)
        _attach(portal, str(assignment["id"]), card_item, _DOC_FRONT, side="front")

        entry = _only(audit_repo, AuditAction.PATIENT_INTAKE_ARTIFACT_UPLOADED)
        assert entry.actor_type == ACTOR_TYPE_PATIENT
        assert entry.resource_id == str(assignment["id"])
        assert entry.changes == {
            "item_id": card_item,
            "document_id": _DOC_FRONT,
            "side": "front",
        }
        assert "card.png" not in str(entry.changes)

    def test_removing_is_recorded_too(
        self,
        portal: TestClient,
        documents: InMemoryPatientDocumentRepository,
        audit_repo: InMemoryAuditRepository,
        assignment: dict[str, object],
        card_item: str,
    ) -> None:
        """A file that arrived and then did not is a fact somebody may need."""
        _upload(documents, _PATIENT_A, document_id=_DOC_FRONT)
        attached = _attach(
            portal, str(assignment["id"]), card_item, _DOC_FRONT, side="front"
        ).json()["artifact"]
        portal.delete(
            f"{_artifacts_url(str(assignment['id']))}/{attached['id']}",
            headers=_auth(_TOKEN_A),
        )

        entry = _only(audit_repo, AuditAction.PATIENT_INTAKE_ARTIFACT_REMOVED)
        assert entry.changes == {"item_id": card_item, "document_id": _DOC_FRONT}

    def test_reading_your_own_form_is_not_audited(
        self,
        portal: TestClient,
        documents: InMemoryPatientDocumentRepository,
        audit_repo: InMemoryAuditRepository,
        assignment: dict[str, object],
        card_item: str,
    ) -> None:
        """The settled model: reading your own record is not a disclosure."""
        _upload(documents, _PATIENT_A, document_id=_DOC_FRONT)
        _attach(portal, str(assignment["id"]), card_item, _DOC_FRONT, side="front")
        before = len(list(audit_repo.list_for_user(_PATIENT_A)))

        portal.get(f"{ASSIGNMENTS}/{assignment['id']}", headers=_auth(_TOKEN_A))

        assert len(list(audit_repo.list_for_user(_PATIENT_A))) == before


class TestTheSaveRouteCannotForgeAnAttachment:
    def test_saving_a_documents_value_is_refused(
        self,
        portal: TestClient,
        assignment: dict[str, object],
        card_item: str,
    ) -> None:
        """The answer names rows, so only the route that writes them may write it.

        Without this the whole ownership check is decoration: a patient
        could put any document id in an answer and completion, which reads
        the answer rather than the rows, would believe it.
        """
        response = portal.put(
            f"{ASSIGNMENTS}/{assignment['id']}/items/{card_item}",
            json={"value": {"documents": [{"document_id": _DOC_B, "side": "front"}]}},
            headers=_auth(_TOKEN_A),
        )
        assert response.status_code == 422

    def test_and_the_form_is_still_not_ready_to_send(
        self,
        portal: TestClient,
        assignment: dict[str, object],
        card_item: str,
    ) -> None:
        portal.put(
            f"{ASSIGNMENTS}/{assignment['id']}/items/{card_item}",
            json={"value": {"documents": [{"document_id": _DOC_B, "side": "front"}]}},
            headers=_auth(_TOKEN_A),
        )
        detail = portal.get(f"{ASSIGNMENTS}/{assignment['id']}", headers=_auth(_TOKEN_A)).json()
        assert card_item in detail["progress"]["missing"]


# ---------------------------------------------------------------------------
# The chart's side: what a clinician sees of the files
# ---------------------------------------------------------------------------
#
# A read of its own rather than a field on the detail route, because the two
# are read at different times: the chart shows the files whenever it is open,
# and the answers only when somebody asks for them. What it adds over the
# patient's own list is the file's own facts — what it is called, what kind it
# is, how big — and the question's wording, so nothing on the chart is named
# by an id.


@pytest.fixture
def chart_patients(mock_user_id: str) -> InMemoryPatientRepository:
    """Both charts, created by the signed-in clinician so they hold a grant."""
    repo = InMemoryPatientRepository()
    now = datetime.now(UTC)
    for patient_id, first, last in ((_PATIENT_A, "Ada", "Lovelace"), (_PATIENT_B, "Grace", "H")):
        repo.create(
            Patient(
                id=patient_id,
                first_name=first,
                last_name=last,
                created_at=now,
                updated_at=now,
            ),
            mock_user_id,
        )
    return repo


@pytest.fixture
def chart(
    client: TestClient,
    assignment_service: IntakeAssignmentService,
    artifact_service: IntakeArtifactService,
    documents: InMemoryPatientDocumentRepository,
    chart_patients: InMemoryPatientRepository,
) -> TestClient:
    """The shared clinician client, with every intake store in memory."""
    real_app.dependency_overrides[get_clinician_intake_assignment_service] = lambda: (
        assignment_service
    )
    real_app.dependency_overrides[get_clinician_intake_artifact_service] = lambda: artifact_service
    real_app.dependency_overrides[get_clinician_patient_document_repository] = lambda: documents
    real_app.dependency_overrides[get_clinician_patient_repository] = lambda: chart_patients
    real_app.dependency_overrides[get_patient_repository] = lambda: chart_patients
    return client


@pytest.fixture
def granted(
    artifacts: InMemoryPatientIntakeArtifactRepository,
    documents: InMemoryPatientDocumentRepository,
    assignments: InMemoryPatientIntakeAssignmentRepository,
    mock_user_id: str,
) -> None:
    """The signed-in clinician's grant on A's chart, and only A's.

    The stores already grant ``_CLINICIAN``, which is the name the portal
    half of this file uses; the clinician who signs in through the shared
    client is somebody else, so the grant has to be given to them by name
    for the refusals below to be about the grant and not about the name.
    """
    for repo in (artifacts, documents, assignments):
        repo.grant_access(_PATIENT_A, mock_user_id)


def _chart_artifacts(chart: TestClient, assignment_id: str, patient_id: str = _PATIENT_A):  # type: ignore[no-untyped-def]
    return chart.get(f"/api/patients/{patient_id}/intake-assignments/{assignment_id}/artifacts")


def _attached(
    portal: TestClient,
    documents: InMemoryPatientDocumentRepository,
    assignment: dict[str, object],
    item_id: str,
    document_id: str,
    *,
    side: str | None = None,
) -> None:
    """Upload a file and attach it, through the portal's own routes."""
    _upload(documents, _PATIENT_A, document_id=document_id)
    response = _attach(portal, str(assignment["id"]), item_id, document_id, side=side)
    assert response.status_code == 201, response.text


class TestTheChartsList:
    def test_it_carries_what_a_clinician_needs_to_open_a_file(
        self,
        chart: TestClient,
        portal: TestClient,
        documents: InMemoryPatientDocumentRepository,
        assignment: dict[str, object],
        card_item: str,
        granted: None,
    ) -> None:
        _attached(portal, documents, assignment, card_item, _DOC_FRONT, side="front")

        response = _chart_artifacts(chart, str(assignment["id"]))
        assert response.status_code == 200, response.text
        body = response.json()
        assert len(body) == 1
        assert body[0] == {
            "id": body[0]["id"],
            "item_id": card_item,
            # The question's own wording, so nothing on the chart is named
            # by an id.
            "item_label": "A photo of your insurance card",
            "side": "front",
            "document_id": _DOC_FRONT,
            "filename": "card.png",
            "content_type": "image/png",
            "size_bytes": 1234,
            # No deployment scans yet, and absent is not "found clean".
            "scan_status": None,
            "created_at": body[0]["created_at"],
        }

    def test_files_come_back_oldest_first(
        self,
        chart: TestClient,
        portal: TestClient,
        documents: InMemoryPatientDocumentRepository,
        assignment: dict[str, object],
        card_item: str,
        records_item: str,
        granted: None,
    ) -> None:
        """So the front of a card reads before the back when it was sent first."""
        _attached(portal, documents, assignment, card_item, _DOC_FRONT, side="front")
        _attached(portal, documents, assignment, card_item, _DOC_BACK, side="back")
        _attached(portal, documents, assignment, records_item, _DOC_RECORD)

        body = _chart_artifacts(chart, str(assignment["id"])).json()
        assert [row["document_id"] for row in body] == [_DOC_FRONT, _DOC_BACK, _DOC_RECORD]
        assert [row["side"] for row in body] == ["front", "back", None]

    def test_a_form_with_nothing_attached_is_an_empty_list(
        self,
        chart: TestClient,
        assignment: dict[str, object],
        granted: None,
    ) -> None:
        """Not a 404: the form exists, it just has no files on it."""
        response = _chart_artifacts(chart, str(assignment["id"]))
        assert response.status_code == 200
        assert response.json() == []


class TestTheChartsDoor:
    def test_a_form_on_another_patients_chart_is_404(
        self,
        chart: TestClient,
        assignment: dict[str, object],
        granted: None,
    ) -> None:
        """The id names a form; the path must not say whose."""
        assert _chart_artifacts(chart, str(assignment["id"]), _PATIENT_B).status_code == 404

    def test_a_form_that_does_not_exist_is_404(self, chart: TestClient, granted: None) -> None:
        assert _chart_artifacts(chart, str(uuid.uuid4())).status_code == 404

    def test_a_clinician_with_no_grant_is_404(
        self,
        chart: TestClient,
        assignment: dict[str, object],
    ) -> None:
        """No ``granted`` fixture, so this caller holds nothing on A's chart."""
        assert _chart_artifacts(chart, str(assignment["id"])).status_code == 404


class TestTheChartsAudit:
    def test_reading_the_files_is_recorded_with_a_count_and_nothing_else(
        self,
        chart: TestClient,
        portal: TestClient,
        documents: InMemoryPatientDocumentRepository,
        assignment: dict[str, object],
        card_item: str,
        granted: None,
        mock_audit_service: AuditService,
    ) -> None:
        _attached(portal, documents, assignment, card_item, _DOC_FRONT, side="front")
        assert _chart_artifacts(chart, str(assignment["id"])).status_code == 200

        viewed = _viewed(mock_audit_service)
        assert len(viewed) == 1
        assert viewed[0].resource_id == str(assignment["id"])
        assert viewed[0].changes == {"count": 1}
        # Never the filename: people name files after what is in them.
        assert "card.png" not in str(viewed[0].changes)

    def test_two_reads_in_one_window_are_one_row(
        self,
        chart: TestClient,
        assignment: dict[str, object],
        granted: None,
        mock_audit_service: AuditService,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """The card on the chart fires this on every visit and every refetch."""
        # One instance across both requests: a fresh one per call would
        # have an empty key set and the gate would never close.
        redis = _OneWindowRedis()
        monkeypatch.setenv("AUDIT_READ_COALESCE_SECONDS", "900")
        monkeypatch.setattr("app.redis_client.get_redis_client", lambda: redis)
        get_settings.cache_clear()
        try:
            _chart_artifacts(chart, str(assignment["id"]))
            _chart_artifacts(chart, str(assignment["id"]))
        finally:
            get_settings.cache_clear()

        assert len(_viewed(mock_audit_service)) == 1


class _OneWindowRedis:
    """Enough of Redis for the coalescing gate: SET NX, never expiring."""

    def __init__(self) -> None:
        self._keys: set[str] = set()

    def set(self, key: str, _value: str, *, nx: bool = False, ex: int | None = None) -> bool | None:
        del ex  # the window never elapses inside one test
        if nx and key in self._keys:
            return None
        self._keys.add(key)
        return True


def _viewed(mock_audit_service: AuditService) -> list[object]:
    return [
        call.args[0]
        for call in mock_audit_service._repo.append.call_args_list
        if call.args[0].action == AuditAction.PATIENT_INTAKE_ARTIFACTS_VIEWED.value
    ]
