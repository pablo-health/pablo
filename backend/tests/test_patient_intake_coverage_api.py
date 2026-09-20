# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Typing the plan off an insurance card, from inside a form.

Two things are under test and they are separate on purpose.

**Where it lands.** The plan a client types at intake goes onto the same
coverage record the chart reads, through the same service the booking form
already uses. There is no second table and no copy on the form, which is
what stops the two disagreeing the first time somebody corrects a digit.

**Whether a payer is asked.** A check is queued only when the deployment
can ask one at all. A self-hoster with no clearinghouse account sees the
card their client photographed and is promised nothing about a payer
nobody can reach; a deployment that has one gets exactly one check, run as
the clinician who asked for the form, and the patient waits for none of it.

The eligibility hook is the real class with its scheduler swapped for a
recorder, so what is asserted is the decision production makes rather than
a stand-in's.
"""

from __future__ import annotations

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
from app.claims.eligibility import IntakeEligibilityCheck, get_intake_eligibility_check
from app.intake.items import ItemDraft
from app.models.audit import ACTOR_TYPE_PATIENT, AuditAction
from app.repositories import (
    InMemoryIntakePacketRepository,
    InMemoryPatientDocumentRepository,
    InMemoryPatientIntakeArtifactRepository,
    InMemoryPatientIntakeAssignmentRepository,
)
from app.repositories.audit import InMemoryAuditRepository
from app.repositories.coverage import (
    InMemoryPatientCoverageRepository,
    InMemoryPayerRepository,
)
from app.routes import patient_intake_assignments
from app.routes.patient_intake_assignments import (
    get_patient_intake_artifact_service,
    get_patient_intake_assignment_service,
)
from app.services.audit_service import AuditService, get_audit_service
from app.services.intake_packet_service import IntakePacketService
from app.services.patient_intake_artifact_service import IntakeArtifactService
from app.services.patient_intake_assignment_service import IntakeAssignmentService
from fastapi import FastAPI
from fastapi.testclient import TestClient

if TYPE_CHECKING:
    from collections.abc import Iterator

    from app.models.eligibility import EligibilityTrigger

_PATIENT_A = "11111111-1111-4111-8111-111111111111"
_TOKEN_A = "credential-of-patient-a"
_CLINICIAN = "clinician-1"

ASSIGNMENTS = "/api/patient/intake/assignments"

_TYPED = {
    "payer_name": "Blue Cross of Somewhere",
    "payer_id": "12345",
    "member_id": "ZGP884401",
    "group_number": "0099",
    "subscriber_relationship": "self",
}


class _Resolver:
    credential_kind = "bearer"

    def resolve(self, credential: PatientCredential) -> PatientContext | None:
        if credential.value != _TOKEN_A:
            return None
        return PatientContext(
            patient_id=_PATIENT_A,
            practice_schema="practice_test_intake",
            credential_kind="bearer",
            auth_strength=AuthStrength.STEPPED_UP,
            session_id="session-handle-a",
        )


class _Scheduler:
    """Stands in for the task queue, and records what was asked of it."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str, EligibilityTrigger]] = []

    def __call__(self, coverage_id: str, user_id: str, trigger: EligibilityTrigger) -> None:
        self.calls.append((coverage_id, user_id, trigger))


def _auth() -> dict[str, str]:
    return {"Authorization": f"Bearer {_TOKEN_A}"}


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def packets() -> InMemoryIntakePacketRepository:
    return InMemoryIntakePacketRepository()


@pytest.fixture
def assignments() -> InMemoryPatientIntakeAssignmentRepository:
    repo = InMemoryPatientIntakeAssignmentRepository()
    repo.grant_access(_PATIENT_A, _CLINICIAN)
    return repo


@pytest.fixture
def coverage() -> InMemoryPatientCoverageRepository:
    return InMemoryPatientCoverageRepository()


@pytest.fixture
def payers() -> InMemoryPayerRepository:
    return InMemoryPayerRepository()


@pytest.fixture
def scheduler() -> _Scheduler:
    return _Scheduler()


@pytest.fixture
def audit_repo() -> InMemoryAuditRepository:
    return InMemoryAuditRepository()


@pytest.fixture
def assignment_service(
    assignments: InMemoryPatientIntakeAssignmentRepository,
    packets: InMemoryIntakePacketRepository,
) -> IntakeAssignmentService:
    return IntakeAssignmentService(assignments, packets)


@pytest.fixture
def artifact_service(
    assignments: InMemoryPatientIntakeAssignmentRepository,
    packets: InMemoryIntakePacketRepository,
    payers: InMemoryPayerRepository,
    coverage: InMemoryPatientCoverageRepository,
) -> IntakeArtifactService:
    return IntakeArtifactService(
        InMemoryPatientIntakeArtifactRepository(),
        assignments,
        packets,
        InMemoryPatientDocumentRepository(),
        payers,
        coverage,
    )


def _app(
    assignment_service: IntakeAssignmentService,
    artifact_service: IntakeArtifactService,
    audit_repo: InMemoryAuditRepository,
    eligibility: IntakeEligibilityCheck,
    monkeypatch: pytest.MonkeyPatch,
) -> FastAPI:
    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(patient_intake_assignments.router)

    registry = PatientResolverRegistry()
    registry.register(_Resolver())
    app.dependency_overrides[get_patient_resolver_registry] = lambda: registry
    app.dependency_overrides[get_patient_intake_assignment_service] = lambda: assignment_service
    app.dependency_overrides[get_patient_intake_artifact_service] = lambda: artifact_service
    app.dependency_overrides[get_intake_eligibility_check] = lambda: eligibility
    app.dependency_overrides[get_audit_service] = lambda: AuditService(audit_repo)

    monkeypatch.setattr(patient_context_module, "get_db_session", object)
    monkeypatch.setattr(patient_context_module, "set_tenant_schema", lambda _s, _schema: None)
    monkeypatch.setattr(patient_context_module, "arm_current_patient_id", lambda _s, _p: None)
    return app


@pytest.fixture
def portal_with_clearinghouse(
    assignment_service: IntakeAssignmentService,
    artifact_service: IntakeArtifactService,
    audit_repo: InMemoryAuditRepository,
    scheduler: _Scheduler,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[TestClient]:
    """A deployment that can ask a payer."""
    eligibility = IntakeEligibilityCheck(available=True, schedule=scheduler)
    with TestClient(
        _app(assignment_service, artifact_service, audit_repo, eligibility, monkeypatch)
    ) as c:
        yield c


@pytest.fixture
def portal_without_clearinghouse(
    assignment_service: IntakeAssignmentService,
    artifact_service: IntakeArtifactService,
    audit_repo: InMemoryAuditRepository,
    scheduler: _Scheduler,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[TestClient]:
    """A self-hoster with no clearinghouse account."""
    eligibility = IntakeEligibilityCheck(available=False, schedule=scheduler)
    with TestClient(
        _app(assignment_service, artifact_service, audit_repo, eligibility, monkeypatch)
    ) as c:
        yield c


@pytest.fixture
def form(packet_service: IntakePacketService) -> str:
    template = packet_service.create_template("Intake", _CLINICIAN)
    version_id = str(packet_service.list_versions(str(template["id"]))[0]["id"])
    packet_service.replace_items(
        version_id,
        [
            ItemDraft(
                key="card",
                item_type="insurance_card",
                label="A photo of your insurance card",
                config={"sides": "both", "collect_fields": True},
            ),
            ItemDraft(
                key="records",
                item_type="document_request",
                label="Any records from a previous provider",
                config={},
            ),
        ],
    )
    packet_service.publish(version_id, _CLINICIAN)
    return version_id


@pytest.fixture
def packet_service(packets: InMemoryIntakePacketRepository) -> IntakePacketService:
    return IntakePacketService(packets)


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


def _coverage_url(assignment_id: str, item_id: str) -> str:
    return f"{ASSIGNMENTS}/{assignment_id}/items/{item_id}/coverage"


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestWhereItLands:
    def test_the_plan_goes_on_the_clients_coverage_record(
        self,
        portal_with_clearinghouse: TestClient,
        coverage: InMemoryPatientCoverageRepository,
        assignment: dict[str, object],
        card_item: str,
    ) -> None:
        """The same row the chart reads, not a copy on the form."""
        response = portal_with_clearinghouse.put(
            _coverage_url(str(assignment["id"]), card_item), json=_TYPED, headers=_auth()
        )

        assert response.status_code == 200
        on_file = coverage.get_active(_PATIENT_A)
        assert on_file is not None
        assert on_file.id == response.json()["coverage_id"]
        assert on_file.member_id == "ZGP884401"
        assert on_file.group_number == "0099"

    def test_the_payer_is_found_or_created_on_the_practices_list(
        self,
        portal_with_clearinghouse: TestClient,
        payers: InMemoryPayerRepository,
        coverage: InMemoryPatientCoverageRepository,
        assignment: dict[str, object],
        card_item: str,
    ) -> None:
        """Same two rows the booking form writes, through the same service."""
        portal_with_clearinghouse.put(
            _coverage_url(str(assignment["id"]), card_item), json=_TYPED, headers=_auth()
        )

        on_file = coverage.get_active(_PATIENT_A)
        assert on_file is not None
        payer = payers.get(on_file.payer_id)
        assert payer is not None
        assert payer.name == "Blue Cross of Somewhere"
        assert payer.payer_id == "12345"

    def test_the_form_itself_holds_no_copy_of_it(
        self,
        portal_with_clearinghouse: TestClient,
        assignment: dict[str, object],
        card_item: str,
    ) -> None:
        """The question's answer is the photograph, not the typed plan."""
        portal_with_clearinghouse.put(
            _coverage_url(str(assignment["id"]), card_item), json=_TYPED, headers=_auth()
        )

        detail = portal_with_clearinghouse.get(
            f"{ASSIGNMENTS}/{assignment['id']}", headers=_auth()
        ).json()
        answer = next(item for item in detail["items"] if item["id"] == card_item)["value"]
        assert answer is None
        # And it is still outstanding: a card is answered by photographing it.
        assert card_item in detail["progress"]["missing"]


class TestTheEligibilityEdge:
    def test_one_check_is_queued_when_the_deployment_can_ask(
        self,
        portal_with_clearinghouse: TestClient,
        scheduler: _Scheduler,
        coverage: InMemoryPatientCoverageRepository,
        assignment: dict[str, object],
        card_item: str,
    ) -> None:
        """Queued, so a slow payer never holds up the person filling the form."""
        response = portal_with_clearinghouse.put(
            _coverage_url(str(assignment["id"]), card_item), json=_TYPED, headers=_auth()
        )

        assert response.json()["eligibility_requested"] is True
        on_file = coverage.get_active(_PATIENT_A)
        assert on_file is not None
        # Exactly one, named for the coverage that was just written, run as
        # the clinician who asked for the form — a patient principal has no
        # reach into a payer.
        assert scheduler.calls == [(on_file.id, _CLINICIAN, "intake")]

    def test_nothing_is_queued_without_one(
        self,
        portal_without_clearinghouse: TestClient,
        scheduler: _Scheduler,
        coverage: InMemoryPatientCoverageRepository,
        assignment: dict[str, object],
        card_item: str,
    ) -> None:
        """The self-hoster's case: the card is on file and nothing is promised."""
        response = portal_without_clearinghouse.put(
            _coverage_url(str(assignment["id"]), card_item), json=_TYPED, headers=_auth()
        )

        assert response.status_code == 200
        assert response.json()["eligibility_requested"] is False
        assert scheduler.calls == []
        # The plan still landed. Not asking a payer is not not recording it.
        assert coverage.get_active(_PATIENT_A) is not None


class TestRefusals:
    def test_a_question_that_does_not_collect_the_fields_is_422(
        self,
        portal_with_clearinghouse: TestClient,
        assignment: dict[str, object],
        records_item: str,
    ) -> None:
        response = portal_with_clearinghouse.put(
            _coverage_url(str(assignment["id"]), records_item), json=_TYPED, headers=_auth()
        )
        assert response.status_code == 422

    def test_a_question_that_is_not_on_this_form_is_404(
        self,
        portal_with_clearinghouse: TestClient,
        assignment: dict[str, object],
    ) -> None:
        response = portal_with_clearinghouse.put(
            _coverage_url(str(assignment["id"]), "33333333-3333-4333-8333-333333333333"),
            json=_TYPED,
            headers=_auth(),
        )
        assert response.status_code == 404

    def test_a_closed_form_is_409(
        self,
        portal_with_clearinghouse: TestClient,
        assignments: InMemoryPatientIntakeAssignmentRepository,
        assignment: dict[str, object],
        card_item: str,
    ) -> None:
        assignments.assignments[str(assignment["id"])]["status"] = "submitted"
        response = portal_with_clearinghouse.put(
            _coverage_url(str(assignment["id"]), card_item), json=_TYPED, headers=_auth()
        )
        assert response.status_code == 409

    def test_a_plan_with_no_member_id_is_refused_by_the_model(
        self,
        portal_with_clearinghouse: TestClient,
        assignment: dict[str, object],
        card_item: str,
    ) -> None:
        """An eligibility check with no member id asks the payer nothing."""
        response = portal_with_clearinghouse.put(
            _coverage_url(str(assignment["id"]), card_item),
            json={"payer_name": "Blue Cross of Somewhere", "member_id": ""},
            headers=_auth(),
        )
        assert response.status_code == 422


class TestAudit:
    def test_the_save_is_recorded_without_the_card(
        self,
        portal_with_clearinghouse: TestClient,
        audit_repo: InMemoryAuditRepository,
        coverage: InMemoryPatientCoverageRepository,
        assignment: dict[str, object],
        card_item: str,
    ) -> None:
        """Which row and which payer. Never the member id or the subscriber."""
        portal_with_clearinghouse.put(
            _coverage_url(str(assignment["id"]), card_item), json=_TYPED, headers=_auth()
        )

        entry = next(
            row
            for row in audit_repo.list_for_user(_PATIENT_A)
            if row.action == AuditAction.PATIENT_COVERAGE_CREATED.value
        )
        on_file = coverage.get_active(_PATIENT_A)
        assert on_file is not None
        assert entry.actor_type == ACTOR_TYPE_PATIENT
        assert entry.resource_id == on_file.id
        assert entry.changes == {
            "source": "intake",
            "payer_id": on_file.payer_id,
            "eligibility_requested": True,
        }
        assert "ZGP884401" not in str(entry.changes)
