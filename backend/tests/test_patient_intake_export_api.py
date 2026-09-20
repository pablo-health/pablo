# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""HTTP-level tests for taking a form out of the product as one file.

The form is built and answered through the services rather than through the
portal's own routes: what is under test here is the document and the door in
front of it, and the round trip the patient makes has its own tests next
door in ``test_patient_intake_assignments_api.py``.

Four things are proven, and each one is a property somebody could lose
without any other test noticing:

* the file is self-contained — no script, no address it would fetch from;
* every value is escaped, including one that is a script tag;
* two exports of one form are the same bytes;
* a form on somebody else's chart is a 404, and every export is audited.
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING, Any

import pytest
from app.intake.signatures import evidence_digest
from app.main import app as real_app
from app.models import Patient
from app.models.audit import AuditAction
from app.outcome_measures.service import OutcomeMeasureService
from app.repositories import (
    InMemoryIntakeDocumentRepository,
    InMemoryIntakePacketRepository,
    InMemoryPatientIntakeAssignmentRepository,
    InMemoryPatientIntakeSignatureRepository,
    InMemoryPatientRepository,
    get_patient_repository,
)
from app.repositories.outcome_measure import InMemoryOutcomeMeasureRepository
from app.routes.patient_intake_assignments import (
    get_clinician_intake_assignment_service,
    get_clinician_patient_repository,
)
from app.routes.patient_intake_export import (
    get_clinician_intake_document_repository,
    get_practice_name,
)
from app.routes.patient_intake_review import (
    get_clinician_signature_repository,
    get_intake_review_service,
)
from app.services.intake_packet_service import IntakePacketService
from app.services.patient_intake_assignment_service import IntakeAssignmentService
from app.services.patient_intake_review_service import IntakeReviewService
from app.utcnow import utc_now

if TYPE_CHECKING:
    from app.services.audit_service import AuditService
    from fastapi.testclient import TestClient

_PATIENT_A = "11111111-1111-4111-8111-111111111111"
_PATIENT_B = "22222222-2222-4222-8222-222222222222"
_CLINICIAN = "test-user-123"
_PRACTICE = "Bramble Street Counseling"

#: An answer that is also a script tag, so escaping is proven against the
#: value a patient could actually type rather than against a fixture that
#: only looks dangerous.
_SCRIPTED = "I want <script>alert('x')</script> it to stop."
_GOALS_LABEL = "What would you like to be <different>?"
_REDONE = "Sleeping through the night."
_NOTE = "Could you say a bit more about when this started?"

# A section, prose, the engine's own question, a written one, a measure, and
# a pair where the second is only asked when the first is answered "no".
_ITEMS: list[dict[str, Any]] = [
    {
        "key": "about",
        "item_type": "section",
        "config": {"title": "About you"},
        "label": None,
        "required": False,
    },
    {
        "key": "welcome",
        "item_type": "instructions",
        "config": {"body_markdown": "Answer what you **can**."},
        "label": None,
        "required": False,
    },
    {"key": "reason", "item_type": "reason", "config": {}, "label": None, "required": True},
    {
        "key": "goals",
        "item_type": "free_text",
        "config": {"max_len": 500},
        "label": _GOALS_LABEL,
        "required": True,
    },
    {"key": "phq9", "item_type": "instrument", "config": {"code": "phq9"}, "label": None},
    {
        "key": "sleeping",
        "item_type": "yes_no",
        "config": {},
        "label": "Are you sleeping through the night?",
        "required": True,
    },
    {
        "key": "sleep_detail",
        "item_type": "free_text",
        "config": {
            "max_len": 500,
            "visible_when": {"item_key": "sleeping", "op": "eq", "value": False},
        },
        "label": "What wakes you?",
        "required": True,
    },
]

_ANSWERS: dict[str, dict[str, object]] = {
    "reason": {"text": "Panic before every shift."},
    "goals": {"text": _SCRIPTED},
    "phq9": {"item_scores": {str(i): 1 for i in range(1, 10)}},
    "sleeping": {"yes": True},
}


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def packets() -> InMemoryIntakePacketRepository:
    return InMemoryIntakePacketRepository()


@pytest.fixture
def assignments_repo() -> InMemoryPatientIntakeAssignmentRepository:
    """Granted to one clinician by name, so "no grant" is a real state here."""
    repo = InMemoryPatientIntakeAssignmentRepository()
    repo.grant_access(_PATIENT_A, _CLINICIAN)
    repo.grant_access(_PATIENT_B, _CLINICIAN)
    return repo


@pytest.fixture
def signatures_repo() -> InMemoryPatientIntakeSignatureRepository:
    return InMemoryPatientIntakeSignatureRepository()


@pytest.fixture
def documents_repo() -> InMemoryIntakeDocumentRepository:
    return InMemoryIntakeDocumentRepository()


@pytest.fixture
def patients() -> InMemoryPatientRepository:
    repo = InMemoryPatientRepository()
    for patient_id, first, last in ((_PATIENT_A, "Ada", "Lovelace"), (_PATIENT_B, "Grace", "H")):
        now = utc_now()
        repo.create(
            Patient(
                id=patient_id,
                first_name=first,
                last_name=last,
                email=f"{first.lower()}@example.com",
                date_of_birth="1990-03-14",
                created_at=now,
                updated_at=now,
            ),
            _CLINICIAN,
        )
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
def published_version(packets: InMemoryIntakePacketRepository) -> str:
    from app.intake.items import ItemDraft  # noqa: PLC0415 — one caller, in a fixture

    service = IntakePacketService(packets)
    template = service.create_template("Intake", _CLINICIAN)
    version_id = str(service.list_versions(str(template["id"]))[0]["id"])
    service.replace_items(
        version_id,
        [
            ItemDraft(
                key=str(item["key"]),
                item_type=str(item["item_type"]),
                config=dict(item["config"]),
                label=item["label"],
                required=bool(item.get("required", True)),
            )
            for item in _ITEMS
        ],
    )
    service.publish(version_id, _CLINICIAN)
    return version_id


@pytest.fixture
def chart(
    client: TestClient,
    service: IntakeAssignmentService,
    reviews: IntakeReviewService,
    patients: InMemoryPatientRepository,
    signatures_repo: InMemoryPatientIntakeSignatureRepository,
    documents_repo: InMemoryIntakeDocumentRepository,
) -> TestClient:
    """The shared clinician client, with every intake store in memory."""
    real_app.dependency_overrides[get_clinician_intake_assignment_service] = lambda: service
    real_app.dependency_overrides[get_clinician_patient_repository] = lambda: patients
    real_app.dependency_overrides[get_patient_repository] = lambda: patients
    real_app.dependency_overrides[get_intake_review_service] = lambda: reviews
    real_app.dependency_overrides[get_clinician_signature_repository] = lambda: signatures_repo
    real_app.dependency_overrides[get_clinician_intake_document_repository] = lambda: documents_repo
    real_app.dependency_overrides[get_practice_name] = lambda: _PRACTICE
    return client


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _item_id(service: IntakeAssignmentService, version_id: str, key: str) -> str:
    return next(str(row["id"]) for row in service.items(version_id) if row["key"] == key)


def _submitted(
    service: IntakeAssignmentService,
    measures: OutcomeMeasureService,
    version_id: str,
    patient_id: str = _PATIENT_A,
) -> dict[str, object]:
    """One assignment, answered and handed in."""
    assignment, _ = service.assign(patient_id, version_id, _CLINICIAN)
    for key, answer in _ANSWERS.items():
        service.save_answer(assignment, patient_id, _item_id(service, version_id, key), answer)
    return service.submit(assignment, patient_id, measures).assignment


def _export(chart: TestClient, assignment_id: str, patient_id: str = _PATIENT_A) -> Any:
    return chart.get(f"/api/patients/{patient_id}/intake-assignments/{assignment_id}/export")


def _sign(
    signatures: InMemoryPatientIntakeSignatureRepository,
    documents: InMemoryIntakeDocumentRepository,
    assignment_id: str,
) -> None:
    """A signature and the document version it names, written straight in.

    The signing route has its own tests; what this exists for is the
    evidence block, and the shortest honest way to have one is a row with
    the digest the signing path would have written.
    """
    version_id = str(uuid.uuid4())
    documents.add(
        {
            "id": version_id,
            "document_key": str(uuid.uuid4()),
            "title": "Consent to treatment",
            "body_markdown": "You may stop at any time.",
            "version": 2,
            "digest": "d" * 64,
            "published_at": utc_now(),
            "published_by": _CLINICIAN,
            "requires_signature": True,
            "signer_roles": ["patient"],
            "created_at": utc_now(),
        }
    )
    row: dict[str, object] = {
        "id": str(uuid.uuid4()),
        "assignment_id": assignment_id,
        "patient_id": _PATIENT_A,
        "item_id": str(uuid.uuid4()),
        "document_version_id": version_id,
        "document_digest": "d" * 64,
        "signer_role": "patient",
        "signer_typed_name": "Ada <Lovelace>",
        "consent_statement_version": "1",
        "signed_at": utc_now(),
        "auth_strength": "stepped_up",
        "session_id": "session-1",
        "ip": "203.0.113.4",
        "user_agent": "Mozilla/5.0",
        "superseded_at": None,
    }
    signatures.add({**row, "evidence_digest": evidence_digest(row)})


def _logged(mock_audit_service: AuditService) -> list[Any]:
    return [call.args[0] for call in mock_audit_service._repo.append.call_args_list]


# ---------------------------------------------------------------------------
# The document
# ---------------------------------------------------------------------------


class TestDocument:
    def test_it_comes_back_as_an_attachment_named_after_the_receipt(
        self,
        chart: TestClient,
        service: IntakeAssignmentService,
        measures: OutcomeMeasureService,
        published_version: str,
    ) -> None:
        assignment = _submitted(service, measures, published_version)
        response = _export(chart, str(assignment["id"]))

        assert response.status_code == 200, response.text
        assert response.headers["content-type"].startswith("text/html")
        assert "charset=utf-8" in response.headers["content-type"]
        receipt = str(assignment["receipt_code"])
        assert response.headers["content-disposition"] == (
            f'attachment; filename="intake-{receipt}.html"'
        )
        assert receipt in response.text

    def test_a_form_nobody_has_handed_in_is_named_after_the_request(
        self,
        chart: TestClient,
        service: IntakeAssignmentService,
        published_version: str,
    ) -> None:
        """No receipt exists until a form is submitted, and none is invented."""
        assignment, _ = service.assign(_PATIENT_A, published_version, _CLINICIAN)
        response = _export(chart, str(assignment["id"]))

        assert response.status_code == 200, response.text
        assert response.headers["content-disposition"] == (
            f'attachment; filename="intake-{assignment["id"]}.html"'
        )

    def test_it_carries_the_practice_the_patient_and_the_form(
        self,
        chart: TestClient,
        service: IntakeAssignmentService,
        measures: OutcomeMeasureService,
        published_version: str,
    ) -> None:
        assignment = _submitted(service, measures, published_version)
        body = _export(chart, str(assignment["id"])).text

        assert _PRACTICE in body
        assert "Ada Lovelace" in body
        assert "1990-03-14" in body
        assert "Intake v1" in body
        assert "Handed in." in body

    def test_it_fetches_nothing_and_runs_nothing(
        self,
        chart: TestClient,
        service: IntakeAssignmentService,
        measures: OutcomeMeasureService,
        published_version: str,
        signatures_repo: InMemoryPatientIntakeSignatureRepository,
        documents_repo: InMemoryIntakeDocumentRepository,
    ) -> None:
        """Self-contained is the whole promise: no script, nothing remote."""
        assignment = _submitted(service, measures, published_version)
        _sign(signatures_repo, documents_repo, str(assignment["id"]))
        body = _export(chart, str(assignment["id"])).text

        assert "<script" not in body.lower()
        for fetches in ("src=", "<iframe", "<link", "http://", "https://"):
            assert fetches not in body.lower()

    def test_an_answer_that_is_a_script_tag_is_escaped(
        self,
        chart: TestClient,
        service: IntakeAssignmentService,
        measures: OutcomeMeasureService,
        published_version: str,
    ) -> None:
        assignment = _submitted(service, measures, published_version)
        body = _export(chart, str(assignment["id"])).text

        assert "<script>alert" not in body
        assert "&lt;script&gt;alert(&#x27;x&#x27;)&lt;/script&gt;" in body
        # The practice's own words go through the same escape.
        assert "&lt;different&gt;" in body

    def test_two_exports_of_one_form_are_the_same_bytes(
        self,
        chart: TestClient,
        service: IntakeAssignmentService,
        measures: OutcomeMeasureService,
        published_version: str,
        signatures_repo: InMemoryPatientIntakeSignatureRepository,
        documents_repo: InMemoryIntakeDocumentRepository,
    ) -> None:
        """What makes a copy checkable against the copy somebody was sent."""
        assignment = _submitted(service, measures, published_version)
        _sign(signatures_repo, documents_repo, str(assignment["id"]))

        first = _export(chart, str(assignment["id"]))
        second = _export(chart, str(assignment["id"]))
        assert first.content == second.content

    def test_a_measure_carries_its_total_and_band(
        self,
        chart: TestClient,
        service: IntakeAssignmentService,
        measures: OutcomeMeasureService,
        published_version: str,
    ) -> None:
        """This is the clinician's own copy, so the score is on it."""
        assignment = _submitted(service, measures, published_version)
        body = _export(chart, str(assignment["id"])).text

        assert "PHQ-9: 9 of 27 (mild)" in body

    def test_a_question_the_patient_was_never_shown_says_so(
        self,
        chart: TestClient,
        service: IntakeAssignmentService,
        measures: OutcomeMeasureService,
        published_version: str,
    ) -> None:
        """Not asked and not answered are different facts about a form."""
        assignment = _submitted(service, measures, published_version)
        body = _export(chart, str(assignment["id"])).text

        assert "What wakes you?" in body
        assert "Not asked." in body

    def test_a_replaced_answer_is_kept_under_the_one_that_replaced_it(
        self,
        chart: TestClient,
        service: IntakeAssignmentService,
        reviews: IntakeReviewService,
        measures: OutcomeMeasureService,
        published_version: str,
    ) -> None:
        """A chart copy that dropped the correction would be the wrong record."""
        assignment = _submitted(service, measures, published_version)
        goals = _item_id(service, published_version, "goals")
        reopened, _event = reviews.request_correction(
            assignment, _CLINICIAN, item_ids=[goals], note=_NOTE
        )
        service.save_answer(reopened, _PATIENT_A, goals, {"text": _REDONE})
        service.submit(
            service.get_for_clinician(str(assignment["id"]), _CLINICIAN) or {},
            _PATIENT_A,
            measures,
        )

        body = _export(chart, str(assignment["id"])).text
        assert _REDONE in body
        assert "&lt;script&gt;" in body
        assert "Replaced" in body
        assert "Answered by the patient" in body
        assert _NOTE in body
        assert "Corrections requested" in body

    def test_an_answer_the_practice_wrote_down_says_who_wrote_it(
        self,
        chart: TestClient,
        service: IntakeAssignmentService,
        reviews: IntakeReviewService,
        measures: OutcomeMeasureService,
        published_version: str,
    ) -> None:
        assignment = _submitted(service, measures, published_version)
        reviews.enter_for_patient(
            assignment,
            _CLINICIAN,
            item_id=_item_id(service, published_version, "reason"),
            value={"text": "Panic, and it started in March."},
        )

        body = _export(chart, str(assignment["id"])).text
        assert "Entered by the practice" in body
        assert "Panic, and it started in March." in body

    def test_a_signature_carries_its_evidence(
        self,
        chart: TestClient,
        service: IntakeAssignmentService,
        measures: OutcomeMeasureService,
        published_version: str,
        signatures_repo: InMemoryPatientIntakeSignatureRepository,
        documents_repo: InMemoryIntakeDocumentRepository,
    ) -> None:
        assignment = _submitted(service, measures, published_version)
        _sign(signatures_repo, documents_repo, str(assignment["id"]))
        body = _export(chart, str(assignment["id"])).text

        assert "Consent to treatment v2" in body
        assert "Ada &lt;Lovelace&gt; (patient)" in body
        assert "By typing my name I agree" in body
        assert "203.0.113.4" in body
        assert "d" * 64 in body

    def test_a_form_with_no_signature_has_no_signature_section(
        self,
        chart: TestClient,
        service: IntakeAssignmentService,
        measures: OutcomeMeasureService,
        published_version: str,
    ) -> None:
        assignment = _submitted(service, measures, published_version)
        assert "Signatures" not in _export(chart, str(assignment["id"])).text


# ---------------------------------------------------------------------------
# The door in front of it
# ---------------------------------------------------------------------------


class TestAccess:
    def test_another_patients_form_is_not_found(
        self,
        chart: TestClient,
        service: IntakeAssignmentService,
        measures: OutcomeMeasureService,
        published_version: str,
    ) -> None:
        """The id names a form; the path must not say whose."""
        assignment = _submitted(service, measures, published_version)
        response = _export(chart, str(assignment["id"]), patient_id=_PATIENT_B)
        assert response.status_code == 404

    def test_a_form_that_does_not_exist_is_not_found(self, chart: TestClient) -> None:
        response = _export(chart, str(uuid.uuid4()))
        assert response.status_code == 404

    def test_the_answer_history_is_read_through_the_grant(
        self,
        service: IntakeAssignmentService,
        measures: OutcomeMeasureService,
        published_version: str,
        assignments_repo: InMemoryPatientIntakeAssignmentRepository,
    ) -> None:
        """The read the document adds carries the grant the others do.

        Asserted against the repository rather than over HTTP: the route
        refuses a clinician with no grant before it reads anything, so the
        only way to see what this read does on its own is to ask it.
        """
        assignment = _submitted(service, measures, published_version)
        assignment_id = str(assignment["id"])

        assert assignments_repo.list_all_responses_for_clinician(assignment_id, _CLINICIAN)
        assert assignments_repo.list_all_responses_for_clinician(assignment_id, "stranger") == []


class TestAudit:
    def test_every_export_is_recorded_without_a_word_of_the_form(
        self,
        chart: TestClient,
        service: IntakeAssignmentService,
        measures: OutcomeMeasureService,
        published_version: str,
        mock_audit_service: AuditService,
    ) -> None:
        assignment = _submitted(service, measures, published_version)
        assert _export(chart, str(assignment["id"])).status_code == 200

        exported = [
            entry
            for entry in _logged(mock_audit_service)
            if entry.action == AuditAction.INTAKE_PACKET_EXPORTED
        ]
        assert len(exported) == 1
        assert exported[0].resource_id == str(assignment["id"])
        assert exported[0].changes == {"version_id": published_version}
        assert _SCRIPTED not in str(exported[0].changes)
