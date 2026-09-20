# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""HTTP-level tests for sending an intake form and filling it in.

Both surfaces are driven here, and each one gets its own app.

The patient half runs through the real ``get_patient_context`` with a
synthesized front door and the session arming patched out, so what is
under test is what the ROUTES do with a principal: which id they trust,
what they refuse, what they write and what the audit says about it.
Two-patient isolation at the database layer is the integration suite's job
(``tests_integration/database/test_patient_intake_assignments_rls.py``);
what can be proven here is the half a policy cannot fix — that no field of
any request names a patient at all.

The clinician half runs on the shared ``client`` fixture, which overrides
the ordinary clinician door.

The audit repository is the real service with its storage swapped, so
``actor_type`` and ``changes`` are the values production writes rather than
what a hand-written double imagined.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
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
from app.models.audit import ACTOR_TYPE_PATIENT, AuditAction, ResourceType
from app.repositories import (
    InMemoryIntakePacketRepository,
    InMemoryPatientIntakeAssignmentRepository,
    InMemoryPatientRepository,
    get_patient_repository,
)
from app.repositories.audit import InMemoryAuditRepository
from app.routes import patient_intake_assignments
from app.routes.patient_intake_assignments import (
    get_clinician_intake_assignment_service,
    get_clinician_patient_repository,
    get_patient_intake_assignment_service,
)
from app.services.audit_service import AuditService, get_audit_service
from app.services.intake_packet_service import IntakePacketService
from app.services.patient_intake_assignment_service import (
    AUDIT_COALESCE_SECONDS,
    IntakeAssignmentService,
)
from app.utcnow import utc_now
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

if TYPE_CHECKING:
    from collections.abc import Iterator

    from app.models.audit import AuditLogEntry

_PATIENT_A = "11111111-1111-4111-8111-111111111111"
_PATIENT_B = "22222222-2222-4222-8222-222222222222"
_TOKEN_A = "credential-of-patient-a"
_TOKEN_B = "credential-of-patient-b"
_TOKEN_A_WEAK = "single-factor-credential-of-patient-a"

ASSIGNMENTS = "/api/patient/intake/assignments"

_PHQ9_COMPLETE = {str(i): 1 for i in range(1, 10)}

# The four questions a practice starts with, as the editor would send them.
_DEFAULT_ITEMS = [
    {"key": "demographics", "item_type": "demographics", "config": {}},
    {"key": "reason", "item_type": "reason", "config": {}},
    {"key": "phq9", "item_type": "instrument", "config": {"code": "phq9"}},
    {"key": "gad7", "item_type": "instrument", "config": {"code": "gad7"}},
]


class _TwoPatientResolver:
    credential_kind = "bearer"

    def resolve(self, credential: PatientCredential) -> PatientContext | None:
        principals = {
            _TOKEN_A: (_PATIENT_A, AuthStrength.STEPPED_UP),
            _TOKEN_B: (_PATIENT_B, AuthStrength.STEPPED_UP),
            _TOKEN_A_WEAK: (_PATIENT_A, AuthStrength.SINGLE_FACTOR),
        }
        found = principals.get(credential.value)
        if found is None:
            return None
        patient_id, strength = found
        return PatientContext(
            patient_id=patient_id,
            practice_schema="practice_test_intake",
            credential_kind="bearer",
            auth_strength=strength,
        )


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _patient(patient_id: str, first: str, last: str) -> Patient:
    now = utc_now()
    return Patient(
        id=patient_id,
        first_name=first,
        last_name=last,
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
def assignments() -> InMemoryPatientIntakeAssignmentRepository:
    repo = InMemoryPatientIntakeAssignmentRepository()
    repo.grant_all_access()
    return repo


@pytest.fixture
def audit_repo() -> InMemoryAuditRepository:
    """The real AuditService with its storage swapped."""
    return InMemoryAuditRepository()


@pytest.fixture
def packet_service(packets: InMemoryIntakePacketRepository) -> IntakePacketService:
    return IntakePacketService(packets)


@pytest.fixture
def published_version(packet_service: IntakePacketService) -> str:
    """A published version of the default form, ready to send."""
    return _publish(packet_service, "Intake", _DEFAULT_ITEMS)


@pytest.fixture
def draft_version(packet_service: IntakePacketService) -> str:
    """A version that was never published, so it cannot be sent."""
    template = packet_service.create_template("Work in progress", "clinician-1")
    return str(packet_service.list_versions(str(template["id"]))[0]["id"])


def _publish(service: IntakePacketService, name: str, items: list[dict[str, Any]]) -> str:
    from app.intake.items import ItemDraft  # noqa: PLC0415 — one caller, in a fixture

    template = service.create_template(name, "clinician-1")
    version_id = str(service.list_versions(str(template["id"]))[0]["id"])
    service.replace_items(
        version_id,
        [
            ItemDraft(key=str(i["key"]), item_type=str(i["item_type"]), config=dict(i["config"]))
            for i in items
        ],
    )
    service.publish(version_id, "clinician-1")
    return version_id


@pytest.fixture
def patients(mock_user_id: str) -> InMemoryPatientRepository:
    """Both charts, created by the signed-in clinician so they hold a grant."""
    repo = InMemoryPatientRepository()
    repo.create(_patient(_PATIENT_A, "Ada", "Lovelace"), mock_user_id)
    repo.create(_patient(_PATIENT_B, "Grace", "Hopper"), mock_user_id)
    return repo


@pytest.fixture
def service(
    assignments: InMemoryPatientIntakeAssignmentRepository,
    packets: InMemoryIntakePacketRepository,
) -> IntakeAssignmentService:
    return IntakeAssignmentService(assignments, packets)


@pytest.fixture
def patient_app(
    service: IntakeAssignmentService,
    audit_repo: InMemoryAuditRepository,
    monkeypatch: pytest.MonkeyPatch,
) -> FastAPI:
    """The patient router alone, with a patient front door and no database."""
    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(patient_intake_assignments.router)

    @app.middleware("http")
    async def _stash_clinician_identity(request: Request, call_next):  # type: ignore[no-untyped-def]
        # What DatabaseSessionMiddleware does once a clinician token
        # verifies. Driven by a header so a test can ask for it.
        if request.headers.get("x-test-clinician-verified"):
            request.state.verified_identity = {"uid": "clinician-1"}
        return await call_next(request)

    registry = PatientResolverRegistry()
    registry.register(_TwoPatientResolver())
    app.dependency_overrides[get_patient_resolver_registry] = lambda: registry
    app.dependency_overrides[get_patient_intake_assignment_service] = lambda: service
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
    patients: InMemoryPatientRepository,
) -> TestClient:
    """The shared clinician client, with the intake stores in memory."""
    real_app.dependency_overrides[get_clinician_intake_assignment_service] = lambda: service
    real_app.dependency_overrides[get_clinician_patient_repository] = lambda: patients
    real_app.dependency_overrides[get_patient_repository] = lambda: patients
    return client


def _entries(audit_repo: InMemoryAuditRepository, actor_id: str) -> list[AuditLogEntry]:
    return list(audit_repo.list_for_user(actor_id))


def _assign(chart: TestClient, patient_id: str, version_id: str) -> Any:
    return chart.post(
        f"/api/patients/{patient_id}/intake-assignments", json={"version_id": version_id}
    )


def _seed_assignment(
    service: IntakeAssignmentService, patient_id: str, version_id: str
) -> dict[str, Any]:
    assignment, _ = service.assign(patient_id, version_id, "clinician-1")
    return dict(assignment)


def _item_id(service: IntakeAssignmentService, version_id: str, key: str) -> str:
    return next(str(row["id"]) for row in service.items(version_id) if row["key"] == key)


# ---------------------------------------------------------------------------
# The patient's door
# ---------------------------------------------------------------------------


class TestPatientAuthentication:
    def test_without_a_credential_is_401(self, portal: TestClient) -> None:
        assert portal.get(ASSIGNMENTS).status_code == 401

    def test_an_unknown_credential_is_401(self, portal: TestClient) -> None:
        assert portal.get(ASSIGNMENTS, headers=_auth("forged")).status_code == 401

    def test_a_clinician_bearer_is_401(self, portal: TestClient) -> None:
        """A verified clinician identity is not a patient principal."""
        response = portal.get(
            ASSIGNMENTS,
            headers={**_auth("clinician-token"), "x-test-clinician-verified": "1"},
        )
        assert response.status_code == 401

    def test_a_single_factor_session_is_403_on_every_route(
        self, portal: TestClient, service: IntakeAssignmentService, published_version: str
    ) -> None:
        assignment = _seed_assignment(service, _PATIENT_A, published_version)
        item_id = _item_id(service, published_version, "reason")
        weak = _auth(_TOKEN_A_WEAK)

        for response in (
            portal.get(ASSIGNMENTS, headers=weak),
            portal.get(f"{ASSIGNMENTS}/{assignment['id']}", headers=weak),
            portal.put(
                f"{ASSIGNMENTS}/{assignment['id']}/items/{item_id}",
                json={"value": {"text": "Panic at work."}},
                headers=weak,
            ),
        ):
            assert response.status_code == 403
            assert response.json()["error"]["code"] == "STEP_UP_REQUIRED"


class TestAnotherPatientsForm:
    def test_naming_bs_assignment_is_a_404(
        self, portal: TestClient, service: IntakeAssignmentService, published_version: str
    ) -> None:
        """Indistinguishable from an id that does not exist."""
        theirs = _seed_assignment(service, _PATIENT_B, published_version)
        response = portal.get(f"{ASSIGNMENTS}/{theirs['id']}", headers=_auth(_TOKEN_A))
        assert response.status_code == 404

    def test_a_cannot_save_an_answer_onto_bs_form(
        self, portal: TestClient, service: IntakeAssignmentService, published_version: str
    ) -> None:
        theirs = _seed_assignment(service, _PATIENT_B, published_version)
        item_id = _item_id(service, published_version, "reason")
        response = portal.put(
            f"{ASSIGNMENTS}/{theirs['id']}/items/{item_id}",
            json={"value": {"text": "not mine to answer"}},
            headers=_auth(_TOKEN_A),
        )
        assert response.status_code == 404

    def test_as_list_holds_only_their_own(
        self, portal: TestClient, service: IntakeAssignmentService, published_version: str
    ) -> None:
        mine = _seed_assignment(service, _PATIENT_A, published_version)
        theirs = _seed_assignment(service, _PATIENT_B, published_version)
        body = portal.get(ASSIGNMENTS, headers=_auth(_TOKEN_A)).json()
        assert [row["id"] for row in body] == [mine["id"]]
        assert theirs["id"] not in str(body)

    def test_a_patient_id_in_the_body_is_refused(
        self, portal: TestClient, service: IntakeAssignmentService, published_version: str
    ) -> None:
        """There is no field to smuggle one through: the model forbids extras."""
        mine = _seed_assignment(service, _PATIENT_A, published_version)
        item_id = _item_id(service, published_version, "reason")
        response = portal.put(
            f"{ASSIGNMENTS}/{mine['id']}/items/{item_id}",
            json={"value": {"text": "Panic at work."}, "patient_id": _PATIENT_B},
            headers=_auth(_TOKEN_A),
        )
        assert response.status_code == 422


# ---------------------------------------------------------------------------
# Saving answers
# ---------------------------------------------------------------------------


class TestSavingAnAnswer:
    def test_a_first_save_moves_the_form_to_in_progress(
        self, portal: TestClient, service: IntakeAssignmentService, published_version: str
    ) -> None:
        assignment = _seed_assignment(service, _PATIENT_A, published_version)
        assert assignment["status"] == "assigned"
        response = portal.put(
            f"{ASSIGNMENTS}/{assignment['id']}/items/"
            f"{_item_id(service, published_version, 'reason')}",
            json={"value": {"text": "Panic at work for about two months."}},
            headers=_auth(_TOKEN_A),
        )
        assert response.status_code == 200
        assert response.json()["status"] == "in_progress"

    def test_saving_the_same_question_twice_updates_one_row(
        self,
        portal: TestClient,
        service: IntakeAssignmentService,
        assignments: InMemoryPatientIntakeAssignmentRepository,
        published_version: str,
    ) -> None:
        """Upsert, not append — a retry after a dropped connection is safe."""
        assignment = _seed_assignment(service, _PATIENT_A, published_version)
        url = (
            f"{ASSIGNMENTS}/{assignment['id']}/items/"
            f"{_item_id(service, published_version, 'reason')}"
        )
        portal.put(url, json={"value": {"text": "First go."}}, headers=_auth(_TOKEN_A))
        portal.put(url, json={"value": {"text": "Second go."}}, headers=_auth(_TOKEN_A))

        live = assignments.list_draft_responses(str(assignment["id"]), _PATIENT_A)
        assert len(live) == 1
        assert live[0]["value"] == {"text": "Second go."}

    def test_progress_comes_back_with_every_save(
        self, portal: TestClient, service: IntakeAssignmentService, published_version: str
    ) -> None:
        """The server's count, so no client has to work it out."""
        assignment = _seed_assignment(service, _PATIENT_A, published_version)
        response = portal.put(
            f"{ASSIGNMENTS}/{assignment['id']}/items/"
            f"{_item_id(service, published_version, 'phq9')}",
            json={"value": {"item_scores": dict(_PHQ9_COMPLETE)}},
            headers=_auth(_TOKEN_A),
        )
        progress = response.json()["progress"]
        assert progress["complete"] is False
        assert len(progress["missing"]) == 3

    def test_answering_everything_reports_complete(
        self, portal: TestClient, service: IntakeAssignmentService, published_version: str
    ) -> None:
        assignment = _seed_assignment(service, _PATIENT_A, published_version)
        answers = {
            "demographics": {"name_confirmed": True, "dob_confirmed": True},
            "reason": {"text": "Panic at work."},
            "phq9": {"item_scores": dict(_PHQ9_COMPLETE)},
            "gad7": {"item_scores": {str(i): 1 for i in range(1, 8)}},
        }
        last: Any = None
        for key, value in answers.items():
            last = portal.put(
                f"{ASSIGNMENTS}/{assignment['id']}/items/"
                f"{_item_id(service, published_version, key)}",
                json={"value": value},
                headers=_auth(_TOKEN_A),
            )
        assert last.json()["progress"] == {"complete": True, "missing": []}

    def test_an_answer_that_does_not_fit_the_question_is_422(
        self, portal: TestClient, service: IntakeAssignmentService, published_version: str
    ) -> None:
        assignment = _seed_assignment(service, _PATIENT_A, published_version)
        response = portal.put(
            f"{ASSIGNMENTS}/{assignment['id']}/items/"
            f"{_item_id(service, published_version, 'phq9')}",
            json={"value": {"item_scores": {"1": 1}}},
            headers=_auth(_TOKEN_A),
        )
        assert response.status_code == 422

    def test_an_item_from_another_form_is_a_404(
        self,
        portal: TestClient,
        service: IntakeAssignmentService,
        packet_service: IntakePacketService,
        published_version: str,
    ) -> None:
        assignment = _seed_assignment(service, _PATIENT_A, published_version)
        other = _publish(
            packet_service, "Other", [{"key": "note", "item_type": "reason", "config": {}}]
        )
        response = portal.put(
            f"{ASSIGNMENTS}/{assignment['id']}/items/{_item_id(service, other, 'note')}",
            json={"value": {"text": "wrong form"}},
            headers=_auth(_TOKEN_A),
        )
        assert response.status_code == 404

    def test_the_detail_view_hands_back_what_was_saved(
        self, portal: TestClient, service: IntakeAssignmentService, published_version: str
    ) -> None:
        assignment = _seed_assignment(service, _PATIENT_A, published_version)
        item_id = _item_id(service, published_version, "reason")
        portal.put(
            f"{ASSIGNMENTS}/{assignment['id']}/items/{item_id}",
            json={"value": {"text": "Panic at work."}},
            headers=_auth(_TOKEN_A),
        )
        body = portal.get(f"{ASSIGNMENTS}/{assignment['id']}", headers=_auth(_TOKEN_A)).json()
        saved = {row["id"]: row["value"] for row in body["items"]}
        assert saved[item_id] == {"text": "Panic at work."}
        assert body["items"][0]["position"] == 0

    def test_saving_onto_a_withdrawn_form_is_409(
        self, portal: TestClient, service: IntakeAssignmentService, published_version: str
    ) -> None:
        assignment = _seed_assignment(service, _PATIENT_A, published_version)
        service.withdraw(str(assignment["id"]), "clinician-1")
        response = portal.put(
            f"{ASSIGNMENTS}/{assignment['id']}/items/"
            f"{_item_id(service, published_version, 'reason')}",
            json={"value": {"text": "too late"}},
            headers=_auth(_TOKEN_A),
        )
        assert response.status_code == 409


# ---------------------------------------------------------------------------
# What the audit log says
# ---------------------------------------------------------------------------


class TestPatientAudit:
    def test_a_save_is_recorded_with_the_question_and_no_answer(
        self,
        portal: TestClient,
        service: IntakeAssignmentService,
        audit_repo: InMemoryAuditRepository,
        published_version: str,
    ) -> None:
        assignment = _seed_assignment(service, _PATIENT_A, published_version)
        item_id = _item_id(service, published_version, "reason")
        secret = "Drinking every night since March."
        portal.put(
            f"{ASSIGNMENTS}/{assignment['id']}/items/{item_id}",
            json={"value": {"text": secret}},
            headers=_auth(_TOKEN_A),
        )

        rows = _entries(audit_repo, _PATIENT_A)
        assert [r.action for r in rows] == [AuditAction.PATIENT_INTAKE_DRAFT_SAVED.value]
        entry = rows[0]
        assert entry.actor_type == ACTOR_TYPE_PATIENT
        assert entry.patient_id == _PATIENT_A
        assert entry.resource_type == ResourceType.PATIENT_INTAKE_ASSIGNMENT.value
        assert entry.resource_id == str(assignment["id"])
        assert entry.changes == {"item_id": item_id}
        assert secret not in str(entry.changes)

    def test_a_burst_of_saves_writes_one_row(
        self,
        portal: TestClient,
        service: IntakeAssignmentService,
        audit_repo: InMemoryAuditRepository,
        published_version: str,
    ) -> None:
        """Coalesced per assignment, so a form does not flood the log."""
        assignment = _seed_assignment(service, _PATIENT_A, published_version)
        for key, value in (
            ("reason", {"text": "Panic at work."}),
            ("phq9", {"item_scores": dict(_PHQ9_COMPLETE)}),
            ("gad7", {"item_scores": {str(i): 1 for i in range(1, 8)}}),
        ):
            portal.put(
                f"{ASSIGNMENTS}/{assignment['id']}/items/"
                f"{_item_id(service, published_version, key)}",
                json={"value": value},
                headers=_auth(_TOKEN_A),
            )
        assert len(_entries(audit_repo, _PATIENT_A)) == 1

    def test_a_save_after_a_gap_is_recorded_again(
        self,
        portal: TestClient,
        service: IntakeAssignmentService,
        assignments: InMemoryPatientIntakeAssignmentRepository,
        audit_repo: InMemoryAuditRepository,
        published_version: str,
    ) -> None:
        """Coming back to a half-filled form is a second visit, and says so."""
        assignment = _seed_assignment(service, _PATIENT_A, published_version)
        portal.put(
            f"{ASSIGNMENTS}/{assignment['id']}/items/"
            f"{_item_id(service, published_version, 'reason')}",
            json={"value": {"text": "Panic at work."}},
            headers=_auth(_TOKEN_A),
        )
        # Age the row past the window, as an afternoon would.
        stored = assignments.assignments[str(assignment["id"])]
        stored["updated_at"] = datetime.now(UTC) - timedelta(seconds=AUDIT_COALESCE_SECONDS + 60)
        portal.put(
            f"{ASSIGNMENTS}/{assignment['id']}/items/"
            f"{_item_id(service, published_version, 'phq9')}",
            json={"value": {"item_scores": dict(_PHQ9_COMPLETE)}},
            headers=_auth(_TOKEN_A),
        )
        assert len(_entries(audit_repo, _PATIENT_A)) == 2

    def test_reading_your_own_form_is_not_audited(
        self,
        portal: TestClient,
        service: IntakeAssignmentService,
        audit_repo: InMemoryAuditRepository,
        published_version: str,
    ) -> None:
        """A patient reading their own record is not a disclosure."""
        assignment = _seed_assignment(service, _PATIENT_A, published_version)
        portal.get(ASSIGNMENTS, headers=_auth(_TOKEN_A))
        portal.get(f"{ASSIGNMENTS}/{assignment['id']}", headers=_auth(_TOKEN_A))
        assert _entries(audit_repo, _PATIENT_A) == []


# ---------------------------------------------------------------------------
# The clinician's side
# ---------------------------------------------------------------------------


class TestAssigning:
    def test_sending_a_form_is_a_201(self, chart: TestClient, published_version: str) -> None:
        response = _assign(chart, _PATIENT_A, published_version)
        assert response.status_code == 201
        body = response.json()
        assert body["status"] == "assigned"
        assert body["packet_name"] == "Intake"
        assert body["version"] == 1
        assert body["progress"]["complete"] is False

    def test_sending_it_twice_returns_the_one_already_live(
        self, chart: TestClient, published_version: str
    ) -> None:
        """A second click, or portal access reissued, is not a second ask."""
        first = _assign(chart, _PATIENT_A, published_version)
        second = _assign(chart, _PATIENT_A, published_version)
        assert second.status_code == 200
        assert second.json()["id"] == first.json()["id"]

    def test_an_unpublished_version_is_422(self, chart: TestClient, draft_version: str) -> None:
        """A form is only ever sent frozen."""
        response = _assign(chart, _PATIENT_A, draft_version)
        assert response.status_code == 422

    def test_an_unknown_version_is_404(self, chart: TestClient) -> None:
        response = _assign(chart, _PATIENT_A, "33333333-3333-4333-8333-333333333333")
        assert response.status_code == 404

    def test_an_unknown_patient_is_404(self, chart: TestClient, published_version: str) -> None:
        response = _assign(chart, "44444444-4444-4444-8444-444444444444", published_version)
        assert response.status_code == 404

    def test_the_send_is_audited_with_the_version_and_nothing_else(
        self,
        chart: TestClient,
        mock_audit_service: AuditService,
        published_version: str,
    ) -> None:
        _assign(chart, _PATIENT_A, published_version)
        logged = [call.args[0] for call in mock_audit_service._repo.append.call_args_list]
        assert [e.action for e in logged] == [AuditAction.PATIENT_INTAKE_ASSIGNED.value]
        assert logged[0].changes == {"version_id": published_version}

    def test_sending_twice_writes_one_audit_row(
        self,
        chart: TestClient,
        mock_audit_service: AuditService,
        published_version: str,
    ) -> None:
        """The second request changed nothing, so it records nothing."""
        _assign(chart, _PATIENT_A, published_version)
        _assign(chart, _PATIENT_A, published_version)
        logged = [call.args[0] for call in mock_audit_service._repo.append.call_args_list]
        assert len(logged) == 1


class TestListingAndWithdrawing:
    def test_the_list_carries_the_form_name_and_progress(
        self, chart: TestClient, published_version: str
    ) -> None:
        _assign(chart, _PATIENT_A, published_version)
        body = chart.get(f"/api/patients/{_PATIENT_A}/intake-assignments").json()
        assert len(body) == 1
        assert body[0]["packet_name"] == "Intake"
        assert body[0]["progress"]["missing"]

    def test_a_patient_with_nothing_sent_is_an_empty_list(self, chart: TestClient) -> None:
        body = chart.get(f"/api/patients/{_PATIENT_B}/intake-assignments").json()
        assert body == []

    def test_withdrawing_flips_the_status(self, chart: TestClient, published_version: str) -> None:
        assignment = _assign(chart, _PATIENT_A, published_version).json()
        response = chart.post(
            f"/api/patients/{_PATIENT_A}/intake-assignments/{assignment['id']}/withdraw"
        )
        assert response.status_code == 200
        assert response.json()["status"] == "withdrawn"

    def test_withdrawing_lets_the_same_form_be_sent_again(
        self, chart: TestClient, published_version: str
    ) -> None:
        """``withdrawn`` sits outside the live set, so asking again is allowed."""
        first = _assign(chart, _PATIENT_A, published_version).json()
        chart.post(f"/api/patients/{_PATIENT_A}/intake-assignments/{first['id']}/withdraw")
        second = _assign(chart, _PATIENT_A, published_version)
        assert second.status_code == 201
        assert second.json()["id"] != first["id"]

    def test_withdrawing_somebody_elses_assignment_is_404(
        self, chart: TestClient, published_version: str
    ) -> None:
        theirs = _assign(chart, _PATIENT_B, published_version).json()
        response = chart.post(
            f"/api/patients/{_PATIENT_A}/intake-assignments/{theirs['id']}/withdraw"
        )
        assert response.status_code == 404

    def test_withdrawing_is_audited(
        self,
        chart: TestClient,
        mock_audit_service: AuditService,
        published_version: str,
    ) -> None:
        assignment = _assign(chart, _PATIENT_A, published_version).json()
        chart.post(f"/api/patients/{_PATIENT_A}/intake-assignments/{assignment['id']}/withdraw")
        logged = [call.args[0] for call in mock_audit_service._repo.append.call_args_list]
        assert logged[-1].action == AuditAction.PATIENT_INTAKE_WITHDRAWN.value
        assert logged[-1].resource_id == assignment["id"]
