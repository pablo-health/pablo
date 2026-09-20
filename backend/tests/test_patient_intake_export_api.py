# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""HTTP-level tests for taking a form out of the product as one file.

The form is built and answered through the services rather than through the
portal's own routes: what is under test here is the document and the door in
front of it, and the round trip the patient makes has its own tests next
door in ``test_patient_intake_assignments_api.py``.

Six things are proven, and each one is a property somebody could lose
without any other test noticing:

* the file is self-contained — no script, and nothing it fetches on open;
* every value is escaped, including one that is a script tag and a
  filename that is one;
* two exports of one form are the same bytes but for the line that says
  when the copy was taken, which is why the clock is a dependency;
* moments are written in the practice's own day, with the zone named;
* the files a form collected are named and linked, never embedded;
* a form on somebody else's chart is a 404, and every export is audited.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any
from zoneinfo import ZoneInfo

import pytest
from app.intake.signatures import evidence_digest
from app.main import app as real_app
from app.models import DocumentCategory, Patient, PatientDocument
from app.models.audit import AuditAction
from app.outcome_measures.service import OutcomeMeasureService
from app.repositories import (
    InMemoryIntakeDocumentRepository,
    InMemoryIntakePacketRepository,
    InMemoryPatientDocumentRepository,
    InMemoryPatientIntakeArtifactRepository,
    InMemoryPatientIntakeAssignmentRepository,
    InMemoryPatientIntakeSignatureRepository,
    InMemoryPatientRepository,
    get_patient_repository,
)
from app.repositories.coverage import (
    InMemoryPatientCoverageRepository,
    InMemoryPayerRepository,
)
from app.repositories.outcome_measure import InMemoryOutcomeMeasureRepository
from app.routes.patient_intake_assignments import (
    get_clinician_intake_assignment_service,
    get_clinician_patient_document_repository,
    get_clinician_patient_repository,
)
from app.routes.patient_intake_export import (
    get_clinician_intake_artifact_repository,
    get_clinician_intake_document_repository,
    get_document_url_base,
    get_export_clock,
    get_practice_name,
    get_practice_timezone,
)
from app.routes.patient_intake_review import (
    get_clinician_signature_repository,
    get_intake_review_service,
)
from app.services.intake_packet_service import IntakePacketService
from app.services.patient_intake_artifact_service import IntakeArtifactService
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

#: The clock the route is handed, so two exports can be compared byte for
#: byte and a second one a minute later can be asked what changed.
_TAKEN = datetime(2026, 3, 14, 17, 5, tzinfo=UTC)
_NEW_YORK = ZoneInfo("America/New_York")
_URL_BASE = "https://pablo.example"

#: A filename that is also a script tag. People name files, so the escape
#: has to hold against one somebody actually typed.
_SCRIPTED_FILENAME = "<script>alert('f')</script>.png"

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
    # Two questions answered by sending a file. Optional, so a form can be
    # handed in with nothing attached and the attachments section can be
    # proven absent as well as present.
    {
        "key": "card",
        "item_type": "insurance_card",
        "config": {"sides": "both"},
        "label": "A photo of your insurance card",
        "required": False,
    },
    {
        "key": "records",
        "item_type": "document_request",
        "config": {},
        "label": "Any records from a previous provider",
        "required": False,
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
def artifacts_repo() -> InMemoryPatientIntakeArtifactRepository:
    repo = InMemoryPatientIntakeArtifactRepository()
    repo.grant_access(_PATIENT_A, _CLINICIAN)
    repo.grant_access(_PATIENT_B, _CLINICIAN)
    return repo


@pytest.fixture
def files_repo() -> InMemoryPatientDocumentRepository:
    """Granted on A's chart only, so "cannot reach it" is a real state.

    Deliberately narrower than the artifact store beside it: that is what
    lets a test put a row on the form whose document this caller may not
    read, which is the case the document has to leave out.
    """
    repo = InMemoryPatientDocumentRepository()
    repo.grant_access(_PATIENT_A, _CLINICIAN)
    return repo


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
def artifact_service(
    artifacts_repo: InMemoryPatientIntakeArtifactRepository,
    assignments_repo: InMemoryPatientIntakeAssignmentRepository,
    packets: InMemoryIntakePacketRepository,
    files_repo: InMemoryPatientDocumentRepository,
) -> IntakeArtifactService:
    """The real attach path, so a test's rows are the ones it writes."""
    return IntakeArtifactService(
        artifacts_repo,
        assignments_repo,
        packets,
        files_repo,
        InMemoryPayerRepository(),
        InMemoryPatientCoverageRepository(),
    )


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
    artifacts_repo: InMemoryPatientIntakeArtifactRepository,
    files_repo: InMemoryPatientDocumentRepository,
) -> TestClient:
    """The shared clinician client, with every intake store in memory.

    The clock is pinned here. It is a dependency precisely so a test can
    do this: the only thing in the document that would otherwise move
    between two calls is the line saying when the copy was taken, and the
    byte-for-byte comparison below is about everything else.
    """
    real_app.dependency_overrides[get_clinician_intake_assignment_service] = lambda: service
    real_app.dependency_overrides[get_clinician_patient_repository] = lambda: patients
    real_app.dependency_overrides[get_patient_repository] = lambda: patients
    real_app.dependency_overrides[get_intake_review_service] = lambda: reviews
    real_app.dependency_overrides[get_clinician_signature_repository] = lambda: signatures_repo
    real_app.dependency_overrides[get_clinician_intake_document_repository] = lambda: documents_repo
    real_app.dependency_overrides[get_clinician_intake_artifact_repository] = lambda: artifacts_repo
    real_app.dependency_overrides[get_clinician_patient_document_repository] = lambda: files_repo
    real_app.dependency_overrides[get_practice_name] = lambda: _PRACTICE
    real_app.dependency_overrides[get_export_clock] = lambda: _TAKEN
    real_app.dependency_overrides[get_practice_timezone] = lambda: UTC
    real_app.dependency_overrides[get_document_url_base] = lambda: _URL_BASE
    return client


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _item_id(service: IntakeAssignmentService, version_id: str, key: str) -> str:
    return next(str(row["id"]) for row in service.items(version_id) if row["key"] == key)


def _hand_in(
    service: IntakeAssignmentService,
    measures: OutcomeMeasureService,
    assignment: dict[str, object],
    version_id: str,
    patient_id: str = _PATIENT_A,
) -> dict[str, object]:
    """Answer the typed questions on a form and send it."""
    for key, answer in _ANSWERS.items():
        service.save_answer(assignment, patient_id, _item_id(service, version_id, key), answer)
    return service.submit(assignment, patient_id, measures).assignment


def _submitted(
    service: IntakeAssignmentService,
    measures: OutcomeMeasureService,
    version_id: str,
    patient_id: str = _PATIENT_A,
) -> dict[str, object]:
    """One assignment, answered and handed in."""
    assignment, _ = service.assign(patient_id, version_id, _CLINICIAN)
    return _hand_in(service, measures, assignment, version_id, patient_id)


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


def _upload(
    files: InMemoryPatientDocumentRepository,
    *,
    filename: str,
    size_bytes: int = 2048,
    patient_id: str = _PATIENT_A,
) -> str:
    """A document as the patient's own upload path leaves one behind."""
    document_id = str(uuid.uuid4())
    now = utc_now()
    files.add(
        PatientDocument(
            id=document_id,
            patient_id=patient_id,
            user_id=None,
            uploaded_by_patient_id=patient_id,
            filename=filename,
            mime_type="image/png",
            gcs_path=f"tenant/intake_artifact/{document_id}",
            size_bytes=size_bytes,
            category=DocumentCategory.INTAKE_ARTIFACT,
            created_at=now,
            finalized_at=now,
        )
    )
    return document_id


def _attach(
    artifacts: IntakeArtifactService,
    files: InMemoryPatientDocumentRepository,
    assignment: dict[str, object],
    item_id: str,
    *,
    filename: str,
    side: str | None = None,
    size_bytes: int = 2048,
    patient_id: str = _PATIENT_A,
) -> str:
    """Upload a file and attach it to a question, the way the portal does.

    Through the real service rather than by writing rows, so the item's
    answer ends up in the shape the attach route actually writes — which
    is what the question's own line on the document is rendered from.
    """
    document_id = _upload(files, filename=filename, size_bytes=size_bytes, patient_id=patient_id)
    artifacts.attach(assignment, patient_id, item_id, document_id, side)
    return document_id


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
        """Self-contained is the whole promise: no script, nothing fetched.

        The one address the document may carry is the link beside an
        attached file, which opens nothing until somebody clicks it — so
        the form under test here has nothing attached, and the file with
        attachments is checked below for the narrower rule.
        """
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

    def test_two_exports_a_minute_apart_differ_only_in_when_they_were_taken(
        self,
        chart: TestClient,
        service: IntakeAssignmentService,
        measures: OutcomeMeasureService,
        published_version: str,
    ) -> None:
        """A copy has to say when it was taken; nothing else may move."""
        assignment = _submitted(service, measures, published_version)
        first = _export(chart, str(assignment["id"])).text.splitlines()

        real_app.dependency_overrides[get_export_clock] = lambda: _TAKEN + timedelta(minutes=1)
        later = _export(chart, str(assignment["id"])).text.splitlines()

        assert len(first) == len(later)
        differing = [(a, b) for a, b in zip(first, later, strict=True) if a != b]
        assert len(differing) == 1
        assert "Exported on 2026-03-14 17:05" in differing[0][0]
        assert "Exported on 2026-03-14 17:06" in differing[0][1]

    def test_moments_are_written_in_the_practices_own_day(
        self,
        chart: TestClient,
        service: IntakeAssignmentService,
        measures: OutcomeMeasureService,
        published_version: str,
    ) -> None:
        """The file is read by the practice that produced it."""
        real_app.dependency_overrides[get_practice_timezone] = lambda: _NEW_YORK
        assignment = _submitted(service, measures, published_version)
        body = _export(chart, str(assignment["id"])).text

        assert "Exported on 2026-03-14 13:05 (America/New_York)" in body
        assert " UTC" not in body
        assert "EDT" in body or "EST" in body

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
# The files the form collected
# ---------------------------------------------------------------------------


class TestAttachedFiles:
    def test_a_form_that_collected_nothing_has_no_attachments_section(
        self,
        chart: TestClient,
        service: IntakeAssignmentService,
        measures: OutcomeMeasureService,
        published_version: str,
    ) -> None:
        """A heading explaining its own absence would be on most charts."""
        assignment = _submitted(service, measures, published_version)
        assert "Attached files" not in _export(chart, str(assignment["id"])).text

    def test_each_file_is_named_sized_and_linked(
        self,
        chart: TestClient,
        service: IntakeAssignmentService,
        artifact_service: IntakeArtifactService,
        files_repo: InMemoryPatientDocumentRepository,
        measures: OutcomeMeasureService,
        published_version: str,
    ) -> None:
        assignment, _ = service.assign(_PATIENT_A, published_version, _CLINICIAN)
        front = _attach(
            artifact_service,
            files_repo,
            assignment,
            _item_id(service, published_version, "card"),
            filename="card-front.png",
            side="front",
        )
        _attach(
            artifact_service,
            files_repo,
            assignment,
            _item_id(service, published_version, "records"),
            filename="referral.pdf",
            size_bytes=5_242_880,
        )
        _hand_in(service, measures, assignment, published_version)

        body = _export(chart, str(assignment["id"])).text

        assert "Attached files" in body
        assert "A photo of your insurance card" in body
        assert "card-front.png (front)" in body
        assert "2.0 KB" in body
        assert "Any records from a previous provider" in body
        assert "referral.pdf ·" in body
        assert "5.0 MB" in body
        assert f'href="{_URL_BASE}/api/documents/{front}/file"' in body

    def test_a_link_is_absolute_even_when_nothing_is_configured(
        self,
        chart: TestClient,
        service: IntakeAssignmentService,
        artifact_service: IntakeArtifactService,
        files_repo: InMemoryPatientDocumentRepository,
        published_version: str,
    ) -> None:
        """A relative path in a file read outside the product resolves
        against whatever opened it."""
        real_app.dependency_overrides.pop(get_document_url_base)
        assignment, _ = service.assign(_PATIENT_A, published_version, _CLINICIAN)
        document = _attach(
            artifact_service,
            files_repo,
            assignment,
            _item_id(service, published_version, "records"),
            filename="referral.pdf",
        )

        body = _export(chart, str(assignment["id"])).text
        assert f'href="http://testserver/api/documents/{document}/file"' in body

    def test_no_file_is_carried_in_the_document(
        self,
        chart: TestClient,
        service: IntakeAssignmentService,
        artifact_service: IntakeArtifactService,
        files_repo: InMemoryPatientDocumentRepository,
        measures: OutcomeMeasureService,
        published_version: str,
    ) -> None:
        """A photograph printed into every copy cannot be un-sent."""
        assignment, _ = service.assign(_PATIENT_A, published_version, _CLINICIAN)
        _attach(
            artifact_service,
            files_repo,
            assignment,
            _item_id(service, published_version, "card"),
            filename="card-front.png",
            side="front",
        )
        _hand_in(service, measures, assignment, published_version)
        body = _export(chart, str(assignment["id"])).text

        assert "<img" not in body.lower()
        assert "data:image" not in body
        assert "src=" not in body

    def test_the_question_that_asked_for_a_file_says_what_arrived(
        self,
        chart: TestClient,
        service: IntakeAssignmentService,
        artifact_service: IntakeArtifactService,
        files_repo: InMemoryPatientDocumentRepository,
        published_version: str,
    ) -> None:
        """And never the stored ids, which mean nothing off this deployment."""
        assignment, _ = service.assign(_PATIENT_A, published_version, _CLINICIAN)
        card = _item_id(service, published_version, "card")
        front = _attach(
            artifact_service,
            files_repo,
            assignment,
            card,
            filename="card-front.png",
            side="front",
        )
        _attach(
            artifact_service,
            files_repo,
            assignment,
            card,
            filename="card-back.png",
            side="back",
        )

        body = _export(chart, str(assignment["id"])).text
        assert "Front of the card sent." in body
        assert "Back of the card sent." in body
        # The id belongs to the link beside the file, and appears nowhere
        # in the questions. A printed page cannot be clicked, so the link
        # is written out as well as linked — hence twice, not once.
        questions, attachments = body.split("<h2>Attached files</h2>")
        assert front not in questions
        assert attachments.count(front) == 2

    def test_a_file_question_with_nothing_on_it_reads_as_unanswered(
        self,
        chart: TestClient,
        service: IntakeAssignmentService,
        measures: OutcomeMeasureService,
        published_version: str,
    ) -> None:
        """Not the raw mapping the column happens to hold."""
        assignment = _submitted(service, measures, published_version)
        body = _export(chart, str(assignment["id"])).text

        assert "A photo of your insurance card" in body
        assert "documents:" not in body
        assert "No answer." in body

    def test_a_filename_that_is_a_script_tag_is_escaped(
        self,
        chart: TestClient,
        service: IntakeAssignmentService,
        artifact_service: IntakeArtifactService,
        files_repo: InMemoryPatientDocumentRepository,
        published_version: str,
    ) -> None:
        """People name files, so the escape has to hold against one."""
        assignment, _ = service.assign(_PATIENT_A, published_version, _CLINICIAN)
        _attach(
            artifact_service,
            files_repo,
            assignment,
            _item_id(service, published_version, "records"),
            filename=_SCRIPTED_FILENAME,
        )
        body = _export(chart, str(assignment["id"])).text

        assert "<script>alert" not in body
        assert "&lt;script&gt;alert(&#x27;f&#x27;)&lt;/script&gt;.png" in body

    def test_a_file_the_caller_cannot_reach_is_left_out(
        self,
        chart: TestClient,
        service: IntakeAssignmentService,
        artifact_service: IntakeArtifactService,
        artifacts_repo: InMemoryPatientIntakeArtifactRepository,
        files_repo: InMemoryPatientDocumentRepository,
        published_version: str,
    ) -> None:
        """Left out rather than reported as unreadable, like the chart list.

        Control first, so what the second assertion proves is the grant and
        not an attachments section that was never going to have anything in
        it. ``files_repo`` grants this clinician A's chart and not B's.
        """
        assignment, _ = service.assign(_PATIENT_A, published_version, _CLINICIAN)
        records = _item_id(service, published_version, "records")
        _attach(
            artifact_service,
            files_repo,
            assignment,
            records,
            filename="mine.png",
        )
        out_of_reach = _upload(files_repo, filename="theirs.png", patient_id=_PATIENT_B)
        artifacts_repo.add(
            {
                "id": str(uuid.uuid4()),
                "assignment_id": str(assignment["id"]),
                "patient_id": _PATIENT_B,
                "item_id": records,
                "document_id": out_of_reach,
                "side": None,
                "created_at": utc_now(),
            }
        )

        body = _export(chart, str(assignment["id"])).text
        assert "mine.png" in body
        assert "theirs.png" not in body
        assert out_of_reach not in body


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
