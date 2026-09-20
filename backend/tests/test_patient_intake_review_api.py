# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""HTTP-level tests for reviewing a form and answering what it says.

Both halves of the review cycle are driven here, each through its own front
door. The clinician half runs on the shared ``client`` fixture; the patient
half runs through the real ``get_patient_context`` with a synthesized front
door, so what is under test is what the ROUTES do with a principal rather
than what a hand-written double was told to allow.

What two-patient isolation means at the database layer is the integration
suite's job (``tests_integration/database/test_patient_intake_review_rls.py``).
What is proven here is the half a row policy cannot fix: the state machine,
the scope of a reopened form, and that every write appends rather than
edits.

The notification is exercised with the capturing stub the engine ships, so
"exactly one link-only payload" is a count over real sends rather than an
assertion about a mock's call list.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

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
from app.main import app as real_app
from app.models import Patient
from app.models.audit import AuditAction
from app.outcome_measures.service import OutcomeMeasureService
from app.portal.delivery import CapturingNoticeDelivery, NoticesNotConfigured
from app.portal.factory import get_notice_delivery
from app.repositories import (
    InMemoryIntakePacketRepository,
    InMemoryPatientDocumentRepository,
    InMemoryPatientIntakeArtifactRepository,
    InMemoryPatientIntakeAssignmentRepository,
    InMemoryPatientIntakeSignatureRepository,
    InMemoryPatientRepository,
    get_patient_repository,
)
from app.repositories.audit import InMemoryAuditRepository
from app.repositories.coverage import (
    InMemoryPatientCoverageRepository,
    InMemoryPayerRepository,
)
from app.repositories.outcome_measure import InMemoryOutcomeMeasureRepository
from app.routes import patient_intake_assignments
from app.routes.patient_intake import get_intake_outcome_measure_service
from app.routes.patient_intake_assignments import (
    get_clinician_intake_assignment_service,
    get_clinician_patient_repository,
    get_patient_intake_artifact_service,
    get_patient_intake_assignment_service,
)
from app.routes.patient_intake_review import (
    get_clinician_signature_repository,
    get_intake_review_service,
)
from app.services.audit_service import AuditService, get_audit_service
from app.services.intake_packet_service import IntakePacketService
from app.services.patient_intake_artifact_service import IntakeArtifactService
from app.services.patient_intake_assignment_service import IntakeAssignmentService
from app.services.patient_intake_review_service import IntakeReviewService
from app.utcnow import utc_now
from fastapi import FastAPI
from fastapi.testclient import TestClient

if TYPE_CHECKING:
    from collections.abc import Iterator

_PATIENT_A = "11111111-1111-4111-8111-111111111111"
_PATIENT_B = "22222222-2222-4222-8222-222222222222"
_TOKEN_A = "credential-of-patient-a"
_CLINICIAN = "clinician-1"

ASSIGNMENTS = "/api/patient/intake/assignments"

_NOTE = "The date you gave for when this started looks like a typo. Have another look?"

# Two written answers and two consent-free questions, so a correction can
# name one and leave the other alone.
_ITEMS = [
    {"key": "reason", "item_type": "reason", "config": {}, "label": None},
    {
        "key": "goals",
        "item_type": "free_text",
        "config": {"max_len": 500},
        "label": "What would you like to be different?",
    },
]


class _PatientAResolver:
    credential_kind = "bearer"

    def resolve(self, credential: PatientCredential) -> PatientContext | None:
        if credential.value != _TOKEN_A:
            return None
        return PatientContext(
            patient_id=_PATIENT_A,
            practice_schema="practice_test_intake",
            credential_kind="bearer",
            auth_strength=AuthStrength.STEPPED_UP,
        )


def _auth() -> dict[str, str]:
    return {"Authorization": f"Bearer {_TOKEN_A}"}


def _patient(patient_id: str, first: str, last: str, email: str) -> Patient:
    now = utc_now()
    return Patient(
        id=patient_id,
        first_name=first,
        last_name=last,
        email=email,
        created_at=now,
        updated_at=now,
        date_of_birth="1990-03-14",
    )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def packets() -> InMemoryIntakePacketRepository:
    return InMemoryIntakePacketRepository()


@pytest.fixture
def assignments_repo() -> InMemoryPatientIntakeAssignmentRepository:
    repo = InMemoryPatientIntakeAssignmentRepository()
    repo.grant_all_access()
    return repo


@pytest.fixture
def signatures_repo() -> InMemoryPatientIntakeSignatureRepository:
    return InMemoryPatientIntakeSignatureRepository()


@pytest.fixture
def audit_repo() -> InMemoryAuditRepository:
    return InMemoryAuditRepository()


@pytest.fixture
def published_version(packets: InMemoryIntakePacketRepository) -> str:
    from app.intake.items import ItemDraft  # noqa: PLC0415 — one caller, in a fixture

    service = IntakePacketService(packets)
    template = service.create_template("Intake", _CLINICIAN)
    version_id = str(service.list_versions(str(template["id"]))[0]["id"])
    service.replace_items(
        version_id,
        [
            ItemDraft(
                key=str(i["key"]),
                item_type=str(i["item_type"]),
                config=dict(i["config"]),  # type: ignore[arg-type]
                label=i["label"],  # type: ignore[arg-type]
            )
            for i in _ITEMS
        ],
    )
    service.publish(version_id, _CLINICIAN)
    return version_id


@pytest.fixture
def patients(mock_user_id: str) -> InMemoryPatientRepository:
    repo = InMemoryPatientRepository()
    repo.create(_patient(_PATIENT_A, "Ada", "Lovelace", "ada@example.com"), mock_user_id)
    repo.create(_patient(_PATIENT_B, "Grace", "Hopper", "grace@example.com"), mock_user_id)
    return repo


@pytest.fixture
def service(
    assignments_repo: InMemoryPatientIntakeAssignmentRepository,
    packets: InMemoryIntakePacketRepository,
) -> IntakeAssignmentService:
    return IntakeAssignmentService(assignments_repo, packets)


@pytest.fixture
def reviews(
    assignments_repo: InMemoryPatientIntakeAssignmentRepository,
    packets: InMemoryIntakePacketRepository,
) -> IntakeReviewService:
    return IntakeReviewService(assignments_repo, packets)


@pytest.fixture
def measures() -> OutcomeMeasureService:
    repo = InMemoryOutcomeMeasureRepository()
    repo.grant_all_access()
    return OutcomeMeasureService(repo)


@pytest.fixture
def notices() -> CapturingNoticeDelivery:
    return CapturingNoticeDelivery()


@pytest.fixture
def artifact_service(
    assignments_repo: InMemoryPatientIntakeAssignmentRepository,
    packets: InMemoryIntakePacketRepository,
) -> IntakeArtifactService:
    """The artifact service on the same in-memory stores the rest uses.

    Present because the form detail read carries what was attached, not
    because anything here attaches a file: the dependency has to resolve.
    """
    return IntakeArtifactService(
        InMemoryPatientIntakeArtifactRepository(),
        assignments_repo,
        packets,
        InMemoryPatientDocumentRepository(),
        InMemoryPayerRepository(),
        InMemoryPatientCoverageRepository(),
    )


@pytest.fixture
def patient_app(
    service: IntakeAssignmentService,
    artifact_service: IntakeArtifactService,
    audit_repo: InMemoryAuditRepository,
    measures: OutcomeMeasureService,
    monkeypatch: pytest.MonkeyPatch,
) -> FastAPI:
    """The patient router alone, with a patient front door and no database."""
    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(patient_intake_assignments.router)

    registry = PatientResolverRegistry()
    registry.register(_PatientAResolver())
    app.dependency_overrides[get_patient_resolver_registry] = lambda: registry
    app.dependency_overrides[get_patient_intake_assignment_service] = lambda: service
    app.dependency_overrides[get_patient_intake_artifact_service] = lambda: artifact_service
    app.dependency_overrides[get_intake_outcome_measure_service] = lambda: measures
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
def chart(
    client: TestClient,
    service: IntakeAssignmentService,
    reviews: IntakeReviewService,
    patients: InMemoryPatientRepository,
    signatures_repo: InMemoryPatientIntakeSignatureRepository,
    notices: CapturingNoticeDelivery,
) -> TestClient:
    """The shared clinician client, with every intake store in memory."""
    real_app.dependency_overrides[get_clinician_intake_assignment_service] = lambda: service
    real_app.dependency_overrides[get_clinician_patient_repository] = lambda: patients
    real_app.dependency_overrides[get_patient_repository] = lambda: patients
    real_app.dependency_overrides[get_intake_review_service] = lambda: reviews
    real_app.dependency_overrides[get_clinician_signature_repository] = lambda: signatures_repo
    real_app.dependency_overrides[get_notice_delivery] = lambda: notices
    return client


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _item_id(service: IntakeAssignmentService, version_id: str, key: str) -> str:
    return next(str(row["id"]) for row in service.items(version_id) if row["key"] == key)


def _base(patient_id: str, assignment_id: str) -> str:
    return f"/api/patients/{patient_id}/intake-assignments/{assignment_id}"


def _submitted(
    portal: TestClient,
    service: IntakeAssignmentService,
    version_id: str,
) -> dict[str, Any]:
    """An assignment with both questions answered and handed in."""
    assignment, _ = service.assign(_PATIENT_A, version_id, _CLINICIAN)
    for key, answer in (("reason", "Panic before every shift."), ("goals", "Sleep.")):
        response = portal.put(
            f"{ASSIGNMENTS}/{assignment['id']}/items/{_item_id(service, version_id, key)}",
            json={"value": {"text": answer}},
            headers=_auth(),
        )
        assert response.status_code == 200, response.text
    handed_in = portal.post(f"{ASSIGNMENTS}/{assignment['id']}/submit", headers=_auth())
    assert handed_in.status_code == 200, handed_in.text
    return dict(assignment)


def _request_correction(
    chart: TestClient, assignment_id: str, item_ids: list[str], note: str = _NOTE
) -> Any:
    return chart.post(
        f"{_base(_PATIENT_A, assignment_id)}/request-correction",
        json={"item_ids": item_ids, "note": note},
    )


def _logged(mock_audit_service: AuditService) -> list[Any]:
    return [call.args[0] for call in mock_audit_service._repo.append.call_args_list]


# ---------------------------------------------------------------------------
# The state machine
# ---------------------------------------------------------------------------


class TestCorrectionStateMachine:
    def test_a_handed_in_form_can_be_reopened(
        self,
        chart: TestClient,
        portal: TestClient,
        service: IntakeAssignmentService,
        published_version: str,
    ) -> None:
        assignment = _submitted(portal, service, published_version)
        response = _request_correction(
            chart, str(assignment["id"]), [_item_id(service, published_version, "reason")]
        )
        assert response.status_code == 200, response.text
        assert response.json()["status"] == "needs_correction"

    def test_a_form_still_being_filled_in_cannot_be_reopened(
        self, chart: TestClient, service: IntakeAssignmentService, published_version: str
    ) -> None:
        """Nothing has been read yet, so there is nothing to ask about."""
        assignment, _ = service.assign(_PATIENT_A, published_version, _CLINICIAN)
        response = _request_correction(
            chart, str(assignment["id"]), [_item_id(service, published_version, "reason")]
        )
        assert response.status_code == 409

    def test_an_accepted_form_cannot_be_reopened(
        self,
        chart: TestClient,
        portal: TestClient,
        service: IntakeAssignmentService,
        published_version: str,
    ) -> None:
        assignment = _submitted(portal, service, published_version)
        chart.post(f"{_base(_PATIENT_A, str(assignment['id']))}/accept")
        response = _request_correction(
            chart, str(assignment["id"]), [_item_id(service, published_version, "reason")]
        )
        assert response.status_code == 409

    def test_naming_a_question_that_is_not_on_the_form_is_422(
        self,
        chart: TestClient,
        portal: TestClient,
        service: IntakeAssignmentService,
        published_version: str,
    ) -> None:
        assignment = _submitted(portal, service, published_version)
        response = _request_correction(
            chart, str(assignment["id"]), ["33333333-3333-4333-8333-333333333333"]
        )
        assert response.status_code == 422

    def test_a_correction_with_no_questions_is_refused(
        self,
        chart: TestClient,
        portal: TestClient,
        service: IntakeAssignmentService,
        published_version: str,
    ) -> None:
        """Reopening nothing would take the form back and give nothing to do."""
        assignment = _submitted(portal, service, published_version)
        assert _request_correction(chart, str(assignment["id"]), []).status_code == 422

    def test_a_correction_with_no_note_is_refused(
        self,
        chart: TestClient,
        portal: TestClient,
        service: IntakeAssignmentService,
        published_version: str,
    ) -> None:
        assignment = _submitted(portal, service, published_version)
        response = _request_correction(
            chart, str(assignment["id"]), [_item_id(service, published_version, "reason")], note=""
        )
        assert response.status_code == 422


class TestAccept:
    def test_a_handed_in_form_can_be_accepted(
        self,
        chart: TestClient,
        portal: TestClient,
        service: IntakeAssignmentService,
        published_version: str,
    ) -> None:
        assignment = _submitted(portal, service, published_version)
        response = chart.post(f"{_base(_PATIENT_A, str(assignment['id']))}/accept")
        assert response.status_code == 200, response.text
        assert response.json()["status"] == "accepted"

    def test_a_form_that_was_never_handed_in_is_409(
        self, chart: TestClient, service: IntakeAssignmentService, published_version: str
    ) -> None:
        assignment, _ = service.assign(_PATIENT_A, published_version, _CLINICIAN)
        response = chart.post(f"{_base(_PATIENT_A, str(assignment['id']))}/accept")
        assert response.status_code == 409

    def test_a_reopened_form_cannot_be_accepted_until_it_comes_back(
        self,
        chart: TestClient,
        portal: TestClient,
        service: IntakeAssignmentService,
        published_version: str,
    ) -> None:
        assignment = _submitted(portal, service, published_version)
        _request_correction(
            chart, str(assignment["id"]), [_item_id(service, published_version, "reason")]
        )
        response = chart.post(f"{_base(_PATIENT_A, str(assignment['id']))}/accept")
        assert response.status_code == 409

    def test_accepting_twice_is_409(
        self,
        chart: TestClient,
        portal: TestClient,
        service: IntakeAssignmentService,
        published_version: str,
    ) -> None:
        assignment = _submitted(portal, service, published_version)
        chart.post(f"{_base(_PATIENT_A, str(assignment['id']))}/accept")
        second = chart.post(f"{_base(_PATIENT_A, str(assignment['id']))}/accept")
        assert second.status_code == 409

    def test_another_patients_assignment_is_404(
        self,
        chart: TestClient,
        portal: TestClient,
        service: IntakeAssignmentService,
        published_version: str,
    ) -> None:
        """The id is real; it is just not on the chart the path names."""
        assignment = _submitted(portal, service, published_version)
        response = chart.post(f"{_base(_PATIENT_B, str(assignment['id']))}/accept")
        assert response.status_code == 404


# ---------------------------------------------------------------------------
# What the patient may do with a reopened form
# ---------------------------------------------------------------------------


class TestCorrectionScope:
    def test_the_named_question_is_answerable_again(
        self,
        chart: TestClient,
        portal: TestClient,
        service: IntakeAssignmentService,
        published_version: str,
    ) -> None:
        assignment = _submitted(portal, service, published_version)
        reason = _item_id(service, published_version, "reason")
        _request_correction(chart, str(assignment["id"]), [reason])

        response = portal.put(
            f"{ASSIGNMENTS}/{assignment['id']}/items/{reason}",
            json={"value": {"text": "Panic before every shift, since about March."}},
            headers=_auth(),
        )
        assert response.status_code == 200, response.text

    def test_a_question_the_practice_did_not_name_is_409(
        self,
        chart: TestClient,
        portal: TestClient,
        service: IntakeAssignmentService,
        published_version: str,
    ) -> None:
        assignment = _submitted(portal, service, published_version)
        _request_correction(
            chart, str(assignment["id"]), [_item_id(service, published_version, "reason")]
        )
        response = portal.put(
            f"{ASSIGNMENTS}/{assignment['id']}/items/"
            f"{_item_id(service, published_version, 'goals')}",
            json={"value": {"text": "Something else entirely."}},
            headers=_auth(),
        )
        assert response.status_code == 409

    def test_the_portal_is_told_what_was_asked_for(
        self,
        chart: TestClient,
        portal: TestClient,
        service: IntakeAssignmentService,
        published_version: str,
    ) -> None:
        assignment = _submitted(portal, service, published_version)
        reason = _item_id(service, published_version, "reason")
        _request_correction(chart, str(assignment["id"]), [reason])

        detail = portal.get(f"{ASSIGNMENTS}/{assignment['id']}", headers=_auth()).json()
        assert detail["correction"]["note"] == _NOTE
        assert detail["correction"]["item_ids"] == [reason]
        assert detail["correction"]["outstanding"] == [reason]

    def test_a_form_nobody_reopened_carries_no_correction(
        self, portal: TestClient, service: IntakeAssignmentService, published_version: str
    ) -> None:
        assignment, _ = service.assign(_PATIENT_A, published_version, _CLINICIAN)
        detail = portal.get(f"{ASSIGNMENTS}/{assignment['id']}", headers=_auth()).json()
        assert detail["correction"] is None

    def test_resubmitting_before_redoing_the_question_is_422(
        self,
        chart: TestClient,
        portal: TestClient,
        service: IntakeAssignmentService,
        published_version: str,
    ) -> None:
        """Finishing the form is not the same as doing what was asked."""
        assignment = _submitted(portal, service, published_version)
        reason = _item_id(service, published_version, "reason")
        _request_correction(chart, str(assignment["id"]), [reason])

        response = portal.post(f"{ASSIGNMENTS}/{assignment['id']}/submit", headers=_auth())
        assert response.status_code == 422
        assert response.json()["error"]["details"]["missing"] == [reason]

    def test_redoing_the_question_lets_the_form_go_back(
        self,
        chart: TestClient,
        portal: TestClient,
        service: IntakeAssignmentService,
        published_version: str,
    ) -> None:
        assignment = _submitted(portal, service, published_version)
        reason = _item_id(service, published_version, "reason")
        _request_correction(chart, str(assignment["id"]), [reason])
        portal.put(
            f"{ASSIGNMENTS}/{assignment['id']}/items/{reason}",
            json={"value": {"text": "Panic before every shift, since about March."}},
            headers=_auth(),
        )

        response = portal.post(f"{ASSIGNMENTS}/{assignment['id']}/submit", headers=_auth())
        assert response.status_code == 200, response.text
        back = chart.get(f"{_base(_PATIENT_A, str(assignment['id']))}/review").json()
        assert back["status"] == "submitted"
        assert [event["kind"] for event in back["events"]] == [
            "correction_requested",
            "corrected",
        ]

    def test_a_form_that_came_back_can_then_be_accepted(
        self,
        chart: TestClient,
        portal: TestClient,
        service: IntakeAssignmentService,
        published_version: str,
    ) -> None:
        assignment = _submitted(portal, service, published_version)
        reason = _item_id(service, published_version, "reason")
        _request_correction(chart, str(assignment["id"]), [reason])
        portal.put(
            f"{ASSIGNMENTS}/{assignment['id']}/items/{reason}",
            json={"value": {"text": "Panic before every shift, since about March."}},
            headers=_auth(),
        )
        portal.post(f"{ASSIGNMENTS}/{assignment['id']}/submit", headers=_auth())

        response = chart.post(f"{_base(_PATIENT_A, str(assignment['id']))}/accept")
        assert response.status_code == 200, response.text


# ---------------------------------------------------------------------------
# Successors
# ---------------------------------------------------------------------------


class TestNothingIsEdited:
    def test_a_correction_leaves_the_handed_in_answer_untouched(
        self,
        chart: TestClient,
        portal: TestClient,
        service: IntakeAssignmentService,
        published_version: str,
        assignments_repo: InMemoryPatientIntakeAssignmentRepository,
    ) -> None:
        assignment = _submitted(portal, service, published_version)
        reason = _item_id(service, published_version, "reason")
        original = next(
            dict(row)
            for row in assignments_repo.responses.values()
            if str(row["item_id"]) == reason
        )

        _request_correction(chart, str(assignment["id"]), [reason])
        portal.put(
            f"{ASSIGNMENTS}/{assignment['id']}/items/{reason}",
            json={"value": {"text": "Panic before every shift, since about March."}},
            headers=_auth(),
        )

        frozen = assignments_repo.responses[str(original["id"])]
        assert frozen["value"] == original["value"]
        assert frozen["draft"] == original["draft"]
        assert frozen["created_at"] == original["created_at"]
        successor = assignments_repo.responses[str(frozen["superseded_by"])]
        assert successor["provenance"] == "patient"
        assert successor["value"] == {"text": "Panic before every shift, since about March."}

    def test_a_clinician_entry_leaves_the_patients_answer_untouched(
        self,
        chart: TestClient,
        portal: TestClient,
        service: IntakeAssignmentService,
        published_version: str,
        assignments_repo: InMemoryPatientIntakeAssignmentRepository,
    ) -> None:
        assignment = _submitted(portal, service, published_version)
        goals = _item_id(service, published_version, "goals")
        original = next(
            dict(row) for row in assignments_repo.responses.values() if str(row["item_id"]) == goals
        )

        response = chart.post(
            f"{_base(_PATIENT_A, str(assignment['id']))}/items/{goals}/clinician-entry",
            json={"value": {"text": "Sleeping through the night."}},
        )
        assert response.status_code == 200, response.text

        frozen = assignments_repo.responses[str(original["id"])]
        assert frozen["value"] == original["value"]
        successor = assignments_repo.responses[str(frozen["superseded_by"])]
        assert successor["provenance"] == "clinician"
        assert successor["draft"] is False

    def test_an_entry_on_a_question_nobody_answered_supersedes_nothing(
        self,
        chart: TestClient,
        service: IntakeAssignmentService,
        published_version: str,
        assignments_repo: InMemoryPatientIntakeAssignmentRepository,
    ) -> None:
        """Intake in the room: there is no patient answer to replace."""
        assignment, _ = service.assign(_PATIENT_A, published_version, _CLINICIAN)
        reason = _item_id(service, published_version, "reason")
        response = chart.post(
            f"{_base(_PATIENT_A, str(assignment['id']))}/items/{reason}/clinician-entry",
            json={"value": {"text": "Told me over the phone."}},
        )
        assert response.status_code == 200, response.text
        rows = [r for r in assignments_repo.responses.values() if str(r["item_id"]) == reason]
        assert len(rows) == 1
        assert rows[0]["superseded_by"] is None


class TestClinicianEntryRefusals:
    def test_an_accepted_form_is_409(
        self,
        chart: TestClient,
        portal: TestClient,
        service: IntakeAssignmentService,
        published_version: str,
    ) -> None:
        assignment = _submitted(portal, service, published_version)
        chart.post(f"{_base(_PATIENT_A, str(assignment['id']))}/accept")
        response = chart.post(
            f"{_base(_PATIENT_A, str(assignment['id']))}/items/"
            f"{_item_id(service, published_version, 'goals')}/clinician-entry",
            json={"value": {"text": "Too late."}},
        )
        assert response.status_code == 409

    def test_a_value_that_does_not_fit_the_question_is_422(
        self, chart: TestClient, service: IntakeAssignmentService, published_version: str
    ) -> None:
        assignment, _ = service.assign(_PATIENT_A, published_version, _CLINICIAN)
        response = chart.post(
            f"{_base(_PATIENT_A, str(assignment['id']))}/items/"
            f"{_item_id(service, published_version, 'goals')}/clinician-entry",
            json={"value": {"text": "x" * 501}},
        )
        assert response.status_code == 422

    def test_a_question_that_is_not_on_the_form_is_404(
        self, chart: TestClient, service: IntakeAssignmentService, published_version: str
    ) -> None:
        assignment, _ = service.assign(_PATIENT_A, published_version, _CLINICIAN)
        response = chart.post(
            f"{_base(_PATIENT_A, str(assignment['id']))}/items/"
            f"33333333-3333-4333-8333-333333333333/clinician-entry",
            json={"value": {"text": "Nowhere to put this."}},
        )
        assert response.status_code == 404

    def test_a_clinician_entered_answer_is_not_the_patients_to_overwrite(
        self,
        chart: TestClient,
        portal: TestClient,
        service: IntakeAssignmentService,
        published_version: str,
    ) -> None:
        """It is the practice's record of what was said, not a draft."""
        assignment, _ = service.assign(_PATIENT_A, published_version, _CLINICIAN)
        reason = _item_id(service, published_version, "reason")
        chart.post(
            f"{_base(_PATIENT_A, str(assignment['id']))}/items/{reason}/clinician-entry",
            json={"value": {"text": "Told me over the phone."}},
        )
        response = portal.put(
            f"{ASSIGNMENTS}/{assignment['id']}/items/{reason}",
            json={"value": {"text": "Actually, something else."}},
            headers=_auth(),
        )
        assert response.status_code == 409


# ---------------------------------------------------------------------------
# The review view
# ---------------------------------------------------------------------------


class TestReviewView:
    def test_every_question_carries_where_its_answer_came_from(
        self,
        chart: TestClient,
        portal: TestClient,
        service: IntakeAssignmentService,
        published_version: str,
    ) -> None:
        assignment = _submitted(portal, service, published_version)
        goals = _item_id(service, published_version, "goals")
        chart.post(
            f"{_base(_PATIENT_A, str(assignment['id']))}/items/{goals}/clinician-entry",
            json={"value": {"text": "Sleeping through the night."}},
        )

        review = chart.get(f"{_base(_PATIENT_A, str(assignment['id']))}/review")
        assert review.status_code == 200, review.text
        by_key = {item["key"]: item for item in review.json()["items"]}
        assert by_key["reason"]["provenance"] == "patient"
        assert by_key["goals"]["provenance"] == "clinician"
        assert by_key["goals"]["superseded_count"] == 1
        assert by_key["reason"]["superseded_count"] == 0

    def test_an_unanswered_question_has_no_provenance(
        self, chart: TestClient, service: IntakeAssignmentService, published_version: str
    ) -> None:
        assignment, _ = service.assign(_PATIENT_A, published_version, _CLINICIAN)
        review = chart.get(f"{_base(_PATIENT_A, str(assignment['id']))}/review").json()
        assert all(item["provenance"] is None for item in review["items"])

    def test_a_form_with_no_consent_document_has_no_signatures(
        self, chart: TestClient, service: IntakeAssignmentService, published_version: str
    ) -> None:
        assignment, _ = service.assign(_PATIENT_A, published_version, _CLINICIAN)
        review = chart.get(f"{_base(_PATIENT_A, str(assignment['id']))}/review").json()
        assert review["signatures"] == []

    def test_the_event_log_reads_in_the_order_things_happened(
        self,
        chart: TestClient,
        portal: TestClient,
        service: IntakeAssignmentService,
        published_version: str,
    ) -> None:
        assignment = _submitted(portal, service, published_version)
        goals = _item_id(service, published_version, "goals")
        chart.post(
            f"{_base(_PATIENT_A, str(assignment['id']))}/items/{goals}/clinician-entry",
            json={"value": {"text": "Sleeping through the night."}},
        )
        chart.post(f"{_base(_PATIENT_A, str(assignment['id']))}/accept")

        events = chart.get(f"{_base(_PATIENT_A, str(assignment['id']))}/review").json()["events"]
        assert [event["kind"] for event in events] == ["clinician_entered", "accepted"]
        assert events[0]["item_ids"] == [goals]

    def test_another_patients_assignment_is_404(
        self,
        chart: TestClient,
        portal: TestClient,
        service: IntakeAssignmentService,
        published_version: str,
    ) -> None:
        assignment = _submitted(portal, service, published_version)
        response = chart.get(f"{_base(_PATIENT_B, str(assignment['id']))}/review")
        assert response.status_code == 404


# ---------------------------------------------------------------------------
# Telling the patient
# ---------------------------------------------------------------------------


class TestNotification:
    def test_one_link_only_payload_when_a_channel_is_wired(
        self,
        chart: TestClient,
        portal: TestClient,
        service: IntakeAssignmentService,
        published_version: str,
        notices: CapturingNoticeDelivery,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _wire_portal_address(monkeypatch)
        assignment = _submitted(portal, service, published_version)
        _request_correction(
            chart, str(assignment["id"]), [_item_id(service, published_version, "reason")]
        )

        assert len(notices.sent) == 1
        sent = notices.sent[0]
        assert sent.to_email == "ada@example.com"
        assert sent.notice == "intake_correction_requested"
        assert sent.link == "https://portal.example.test/portal/a-practice"
        # Nothing about the form, the question or the note travels with it.
        assert _NOTE not in sent.link

    def test_nothing_is_sent_when_no_channel_is_wired(
        self,
        client: TestClient,
        portal: TestClient,
        service: IntakeAssignmentService,
        reviews: IntakeReviewService,
        patients: InMemoryPatientRepository,
        published_version: str,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """The default. The request is still recorded."""
        real_app.dependency_overrides[get_clinician_intake_assignment_service] = lambda: service
        real_app.dependency_overrides[get_clinician_patient_repository] = lambda: patients
        real_app.dependency_overrides[get_patient_repository] = lambda: patients
        real_app.dependency_overrides[get_intake_review_service] = lambda: reviews
        real_app.dependency_overrides[get_notice_delivery] = NoticesNotConfigured
        _wire_portal_address(monkeypatch)

        assignment = _submitted(portal, service, published_version)
        response = _request_correction(
            client, str(assignment["id"]), [_item_id(service, published_version, "reason")]
        )
        assert response.status_code == 200, response.text
        assert response.json()["status"] == "needs_correction"

    def test_a_chart_with_no_email_address_is_not_a_failure(
        self,
        chart: TestClient,
        portal: TestClient,
        service: IntakeAssignmentService,
        published_version: str,
        patients: InMemoryPatientRepository,
        notices: CapturingNoticeDelivery,
        mock_user_id: str,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _wire_portal_address(monkeypatch)
        patients.create(_patient(_PATIENT_A, "Ada", "Lovelace", ""), mock_user_id)
        assignment = _submitted(portal, service, published_version)
        response = _request_correction(
            chart, str(assignment["id"]), [_item_id(service, published_version, "reason")]
        )
        assert response.status_code == 200, response.text
        assert notices.sent == []


def _wire_portal_address(monkeypatch: pytest.MonkeyPatch) -> None:
    """A portal origin and a practice slug, so a link can be minted.

    Patched at the two seams :mod:`app.portal.notices` reads rather than by
    standing up a practice: what is under test here is which payload is
    sent, not how a practice gets an address.
    """
    from app.portal import notices as notices_module  # noqa: PLC0415 — test-local seam
    from app.portal.practice_routes import PracticeAddress  # noqa: PLC0415

    monkeypatch.setattr(
        notices_module, "_resolve_practice_from_email", lambda _email: ("practice-1", "schema")
    )
    monkeypatch.setattr(
        notices_module,
        "ensure_practice_slug",
        lambda _pid: PracticeAddress(slug="a-practice", display_name="A Practice", enabled=True),
    )
    monkeypatch.setattr(
        notices_module,
        "build_portal_link",
        lambda *, slug: f"https://portal.example.test/portal/{slug}",
    )


# ---------------------------------------------------------------------------
# What the record says
# ---------------------------------------------------------------------------


class TestAudit:
    def test_a_correction_records_the_questions_and_not_the_note(
        self,
        chart: TestClient,
        portal: TestClient,
        service: IntakeAssignmentService,
        published_version: str,
        mock_audit_service: AuditService,
    ) -> None:
        assignment = _submitted(portal, service, published_version)
        reason = _item_id(service, published_version, "reason")
        _request_correction(chart, str(assignment["id"]), [reason])

        entry = _logged(mock_audit_service)[-1]
        assert entry.action == AuditAction.INTAKE_CORRECTION_REQUESTED.value
        assert entry.resource_id == str(assignment["id"])
        assert entry.changes == {"item_ids": [reason]}
        assert _NOTE not in str(entry.changes)

    def test_accepting_is_recorded(
        self,
        chart: TestClient,
        portal: TestClient,
        service: IntakeAssignmentService,
        published_version: str,
        mock_audit_service: AuditService,
    ) -> None:
        assignment = _submitted(portal, service, published_version)
        chart.post(f"{_base(_PATIENT_A, str(assignment['id']))}/accept")
        assert _logged(mock_audit_service)[-1].action == AuditAction.INTAKE_ACCEPTED.value

    def test_an_entry_records_the_question_and_not_the_value(
        self,
        chart: TestClient,
        service: IntakeAssignmentService,
        published_version: str,
        mock_audit_service: AuditService,
    ) -> None:
        assignment, _ = service.assign(_PATIENT_A, published_version, _CLINICIAN)
        goals = _item_id(service, published_version, "goals")
        chart.post(
            f"{_base(_PATIENT_A, str(assignment['id']))}/items/{goals}/clinician-entry",
            json={"value": {"text": "Sleeping through the night."}},
        )
        entry = _logged(mock_audit_service)[-1]
        assert entry.action == AuditAction.INTAKE_CLINICIAN_ENTRY.value
        assert entry.changes == {"item_id": goals}

    def test_reading_the_review_is_a_disclosure(
        self,
        chart: TestClient,
        portal: TestClient,
        service: IntakeAssignmentService,
        published_version: str,
        mock_audit_service: AuditService,
    ) -> None:
        assignment = _submitted(portal, service, published_version)
        chart.get(f"{_base(_PATIENT_A, str(assignment['id']))}/review")
        entry = _logged(mock_audit_service)[-1]
        assert entry.action == AuditAction.INTAKE_REVIEW_VIEWED.value
        assert entry.changes == {"count": 2}

    def test_handing_a_reopened_form_back_is_recorded_as_the_patient(
        self,
        chart: TestClient,
        portal: TestClient,
        service: IntakeAssignmentService,
        published_version: str,
        audit_repo: InMemoryAuditRepository,
    ) -> None:
        assignment = _submitted(portal, service, published_version)
        reason = _item_id(service, published_version, "reason")
        _request_correction(chart, str(assignment["id"]), [reason])
        portal.put(
            f"{ASSIGNMENTS}/{assignment['id']}/items/{reason}",
            json={"value": {"text": "Panic before every shift, since about March."}},
            headers=_auth(),
        )
        portal.post(f"{ASSIGNMENTS}/{assignment['id']}/submit", headers=_auth())

        actions = [entry.action for entry in audit_repo.list_for_user(_PATIENT_A)]
        assert AuditAction.PATIENT_INTAKE_CORRECTED.value in actions
        assert AuditAction.PATIENT_INTAKE_SUBMITTED.value in actions

    def test_an_ordinary_submission_records_no_correction(
        self,
        portal: TestClient,
        service: IntakeAssignmentService,
        published_version: str,
        audit_repo: InMemoryAuditRepository,
    ) -> None:
        _submitted(portal, service, published_version)
        actions = [entry.action for entry in audit_repo.list_for_user(_PATIENT_A)]
        assert AuditAction.PATIENT_INTAKE_CORRECTED.value not in actions
