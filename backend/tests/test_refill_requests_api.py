# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""HTTP-level tests for refill requests.

The patient is a real principal, resolved through the actual
``get_patient_context`` dependency with a two-patient resolver and the
session arming patched out, the same harness the secure-messaging suite
uses. The clinician is the ordinary user dependency with explicit grants.

``refills`` is not in the default ``PORTAL_MODULES``, so the shared app does
not mount the patient router. These tests mount the two refill routers on
an app of their own that shares the shared app's dependency overrides and
error handlers — the real routes, not a re-implementation of them. That the
patient router stays unmounted unless configured is asserted at the end.
"""

from __future__ import annotations

from datetime import timedelta
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
from app.main import app, portal_module_routers
from app.models.audit import ACTOR_TYPE_CLINICIAN, ACTOR_TYPE_PATIENT, AuditAction
from app.models.refill_request import RefillRequest
from app.portal.modules import MODULE_MARKER_PATHS
from app.rate_limit import REFILL_REQUESTS_PER_MINUTE, reset_refill_request_limiter
from app.repositories import InMemoryRefillRequestRepository
from app.routes.refill_requests import (
    get_clinician_refill_request_repository,
    get_refill_request_repository,
    patient_refills_router,
    refill_requests_router,
)
from app.services.refill_request_hooks import (
    RefillRequestEvent,
    get_refill_request_hook_registry,
)
from app.utcnow import utc_now
from fastapi import FastAPI
from fastapi.testclient import TestClient

if TYPE_CHECKING:
    from app.services import AuditService
    from app.services.refill_request_hooks import RefillRequestHookRegistry

_PATIENT_A = "patient-a"
_PATIENT_B = "patient-b"
_TOKEN_A = "credential-of-patient-a"
_TOKEN_B = "credential-of-patient-b"

_MED_A = "med-a-sertraline"
_MED_A_STOPPED = "med-a-stopped"
_MED_B = "med-b-lithium"

PATIENT_BASE = "/api/patient/refills"
CLINICIAN_BASE = "/api/refill-requests"


class _TwoPatientResolver:
    credential_kind = "bearer"

    def resolve(self, credential: PatientCredential) -> PatientContext | None:
        patient_id = {_TOKEN_A: _PATIENT_A, _TOKEN_B: _PATIENT_B}.get(credential.value)
        if patient_id is None:
            return None
        return PatientContext(
            patient_id=patient_id,
            practice_schema="practice_test",
            credential_kind="bearer",
            auth_strength=AuthStrength.STEPPED_UP,
        )


@pytest.fixture
def refill_repo() -> InMemoryRefillRequestRepository:
    repo = InMemoryRefillRequestRepository()
    repo.add_patient(_PATIENT_A, "Ada", "Lovelace", preferred_name="Addie")
    repo.add_patient(_PATIENT_B, "Grace", "Hopper")
    repo.add_medication(_MED_A, _PATIENT_A, "Sertraline", "50 mg")
    repo.add_medication(_MED_A_STOPPED, _PATIENT_A, "Bupropion", "150 mg", active=False)
    repo.add_medication(_MED_B, _PATIENT_B, "Lithium", "300 mg")
    return repo


@pytest.fixture(autouse=True)
def hooks() -> RefillRequestHookRegistry:
    registry = get_refill_request_hook_registry()
    registry.clear()
    yield registry
    registry.clear()


@pytest.fixture
def refill_client(
    client: TestClient,
    refill_repo: InMemoryRefillRequestRepository,
    monkeypatch: pytest.MonkeyPatch,
) -> TestClient:
    """Both refill routers on one app, sharing the shared app's overrides.

    Depends on ``client`` for its side effect: that fixture installs the
    clinician, tenant and audit overrides on ``app.dependency_overrides``,
    and this app reads the same dict.
    """
    reset_refill_request_limiter()
    registry = PatientResolverRegistry()
    registry.register(_TwoPatientResolver())
    app.dependency_overrides[get_patient_resolver_registry] = lambda: registry
    app.dependency_overrides[get_refill_request_repository] = lambda: refill_repo
    app.dependency_overrides[get_clinician_refill_request_repository] = lambda: refill_repo
    monkeypatch.setattr(patient_context_module, "get_db_session", object)
    monkeypatch.setattr(patient_context_module, "set_tenant_schema", lambda _s, _schema: None)
    monkeypatch.setattr(patient_context_module, "arm_current_patient_id", lambda _s, _p: None)

    refill_app = FastAPI()
    refill_app.include_router(patient_refills_router)
    refill_app.include_router(refill_requests_router)
    register_exception_handlers(refill_app)
    refill_app.dependency_overrides = app.dependency_overrides
    return TestClient(refill_app)


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _ask(client: TestClient, token: str = _TOKEN_A, **body: object) -> dict:
    payload = body or {"medication_id": _MED_A}
    response = client.post(PATIENT_BASE, json=payload, headers=_auth(token))
    assert response.status_code == 201, response.text
    return response.json()


def _entries(audit: AuditService) -> list:
    return [call.args[0] for call in audit._repo.append.call_args_list]


# ---------------------------------------------------------------------------
# Patient surface
# ---------------------------------------------------------------------------


class TestPatientMedications:
    def test_lists_only_their_own_active_medications(self, refill_client: TestClient) -> None:
        response = refill_client.get(f"{PATIENT_BASE}/medications", headers=_auth(_TOKEN_A))

        assert response.status_code == 200, response.text
        assert response.json() == {
            "data": [{"id": _MED_A, "drug_name": "Sertraline", "dose": "50 mg"}],
            "total": 1,
        }

    def test_requires_a_patient_principal(self, refill_client: TestClient) -> None:
        assert refill_client.get(f"{PATIENT_BASE}/medications").status_code == 401


class TestPatientAsks:
    def test_from_their_list_copies_the_name_and_dose(self, refill_client: TestClient) -> None:
        body = _ask(refill_client, medication_id=_MED_A, pharmacy_text="Main St", patient_note="x")

        assert body["medication_id"] == _MED_A
        assert body["medication_text"] == "Sertraline 50 mg"
        assert body["pharmacy_text"] == "Main St"
        assert body["status"] == "requested"
        assert body["decided_at"] is None
        assert "prescriber_note" not in body
        assert "patient_id" not in body

    def test_by_typing_a_name(self, refill_client: TestClient) -> None:
        body = _ask(refill_client, medication_text="  Hydroxyzine 25 mg  ")

        assert body["medication_id"] is None
        assert body["medication_text"] == "Hydroxyzine 25 mg"

    @pytest.mark.parametrize(
        "payload",
        [
            {},
            {"medication_id": _MED_A, "medication_text": "Sertraline"},
            {"medication_text": ""},
            {"medication_text": "x" * 201},
            {"medication_id": _MED_A, "patient_note": "x" * 2_001},
        ],
    )
    def test_malformed_requests_are_refused(
        self, refill_client: TestClient, payload: dict[str, object]
    ) -> None:
        response = refill_client.post(PATIENT_BASE, json=payload, headers=_auth(_TOKEN_A))
        assert response.status_code == 422, response.text

    @pytest.mark.parametrize("medication_id", [_MED_B, _MED_A_STOPPED, "no-such-med"])
    def test_a_medication_that_is_not_theirs_and_active_is_a_404(
        self, refill_client: TestClient, medication_id: str
    ) -> None:
        response = refill_client.post(
            PATIENT_BASE, json={"medication_id": medication_id}, headers=_auth(_TOKEN_A)
        )
        assert response.status_code == 404, response.text

    def test_a_patient_id_or_status_in_the_body_is_ignored(
        self, refill_client: TestClient, refill_repo: InMemoryRefillRequestRepository
    ) -> None:
        body = _ask(
            refill_client,
            medication_text="Sertraline",
            patient_id=_PATIENT_B,
            status="approved",
        )

        assert body["status"] == "requested"
        assert [r.id for r in refill_repo.list_for_patient(_PATIENT_A)] == [body["id"]]
        assert refill_repo.list_for_patient(_PATIENT_B) == []

    def test_they_see_their_own_requests_newest_first_and_nobody_elses(
        self, refill_client: TestClient
    ) -> None:
        first = _ask(refill_client, medication_text="First")
        second = _ask(refill_client, medication_text="Second")
        _ask(refill_client, _TOKEN_B, medication_id=_MED_B)

        mine = refill_client.get(PATIENT_BASE, headers=_auth(_TOKEN_A)).json()
        assert [r["id"] for r in mine["data"]] == [second["id"], first["id"]]
        assert mine["total"] == 2

    def test_asking_is_audited_with_the_patient_as_actor_and_no_content(
        self, refill_client: TestClient, mock_audit_service: AuditService
    ) -> None:
        body = _ask(refill_client, medication_id=_MED_A, patient_note="I ran out early")

        created = [e for e in _entries(mock_audit_service) if e.action == "refill_request_created"]
        assert len(created) == 1
        entry = created[0]
        assert entry.actor_type == ACTOR_TYPE_PATIENT
        assert entry.patient_id == _PATIENT_A
        assert entry.resource_id == body["id"]
        assert entry.changes == {"from_medication_list": True, "has_note": True}
        assert "Sertraline" not in str(entry.changes)
        assert "ran out" not in str(entry.changes)

    def test_reading_their_own_requests_is_not_audited(
        self, refill_client: TestClient, mock_audit_service: AuditService
    ) -> None:
        refill_client.get(PATIENT_BASE, headers=_auth(_TOKEN_A))
        refill_client.get(f"{PATIENT_BASE}/medications", headers=_auth(_TOKEN_A))
        assert _entries(mock_audit_service) == []

    def test_asking_is_rate_limited_per_patient(self, refill_client: TestClient) -> None:
        for _ in range(REFILL_REQUESTS_PER_MINUTE):
            _ask(refill_client, medication_text="Sertraline")

        over = refill_client.post(
            PATIENT_BASE, json={"medication_text": "Sertraline"}, headers=_auth(_TOKEN_A)
        )
        assert over.status_code == 429
        # Another patient's budget is their own.
        _ask(refill_client, _TOKEN_B, medication_id=_MED_B)


class TestSubmittedHook:
    def test_fires_once_with_the_request(
        self, refill_client: TestClient, hooks: RefillRequestHookRegistry
    ) -> None:
        seen: list[RefillRequestEvent] = []
        hooks.register(seen.append)

        body = _ask(refill_client, medication_id=_MED_A, patient_note="note")

        assert len(seen) == 1
        event = seen[0]
        assert event.kind == "submitted"
        assert event.request_id == body["id"]
        assert event.patient_id == _PATIENT_A
        assert event.practice_schema == "practice_test"
        assert event.medication_text == "Sertraline 50 mg"
        assert event.patient_note == "note"
        assert event.decided_at is None

    def test_a_raising_hook_does_not_fail_the_request(
        self, refill_client: TestClient, hooks: RefillRequestHookRegistry
    ) -> None:
        seen: list[RefillRequestEvent] = []

        def broken(_event: RefillRequestEvent) -> None:
            raise RuntimeError("downstream is down")

        hooks.register(broken)
        hooks.register(seen.append)

        _ask(refill_client, medication_text="Sertraline")
        assert len(seen) == 1


# ---------------------------------------------------------------------------
# Clinician surface
# ---------------------------------------------------------------------------


def _seed(
    repo: InMemoryRefillRequestRepository,
    request_id: str,
    patient_id: str,
    *,
    minutes_ago: int,
    status: str = "requested",
) -> None:
    created = utc_now() - timedelta(minutes=minutes_ago)
    repo.add(
        RefillRequest(
            id=request_id,
            patient_id=patient_id,
            medication_id=None,
            medication_text="Sertraline 50 mg",
            pharmacy_text=None,
            patient_note=None,
            status=status,
            created_at=created,
            updated_at=created,
            decided_at=None if status == "requested" else created,
        )
    )


class TestClinicianQueue:
    def test_pending_is_oldest_first_and_only_granted_patients(
        self,
        refill_client: TestClient,
        refill_repo: InMemoryRefillRequestRepository,
        mock_user_id: str,
    ) -> None:
        _seed(refill_repo, "newer", _PATIENT_A, minutes_ago=5)
        _seed(refill_repo, "older", _PATIENT_A, minutes_ago=50)
        _seed(refill_repo, "answered", _PATIENT_A, minutes_ago=90, status="approved")
        _seed(refill_repo, "not-mine", _PATIENT_B, minutes_ago=100)
        refill_repo.grant_access(_PATIENT_A, mock_user_id)

        body = refill_client.get(CLINICIAN_BASE).json()

        assert [r["id"] for r in body["data"]] == ["older", "newer"]
        assert body["data"][0]["patient_name"] == "Addie Lovelace"
        assert body["data"][0]["patient_id"] == _PATIENT_A

    def test_recent_is_what_was_answered(
        self,
        refill_client: TestClient,
        refill_repo: InMemoryRefillRequestRepository,
        mock_user_id: str,
    ) -> None:
        _seed(refill_repo, "waiting", _PATIENT_A, minutes_ago=5)
        _seed(refill_repo, "answered", _PATIENT_A, minutes_ago=9, status="declined")
        refill_repo.grant_access(_PATIENT_A, mock_user_id)

        body = refill_client.get(CLINICIAN_BASE, params={"view": "recent"}).json()
        assert [r["id"] for r in body["data"]] == ["answered"]

    def test_an_unknown_view_is_refused(self, refill_client: TestClient) -> None:
        assert refill_client.get(CLINICIAN_BASE, params={"view": "all"}).status_code == 422

    def test_the_queue_is_audited_once_per_patient_on_it(
        self,
        refill_client: TestClient,
        refill_repo: InMemoryRefillRequestRepository,
        mock_user_id: str,
        mock_audit_service: AuditService,
    ) -> None:
        _seed(refill_repo, "a1", _PATIENT_A, minutes_ago=5)
        _seed(refill_repo, "a2", _PATIENT_A, minutes_ago=6)
        _seed(refill_repo, "b1", _PATIENT_B, minutes_ago=7)
        refill_repo.grant_access(_PATIENT_A, mock_user_id)
        refill_repo.grant_access(_PATIENT_B, mock_user_id)

        refill_client.get(CLINICIAN_BASE)

        viewed = {
            e.patient_id: e.changes
            for e in _entries(mock_audit_service)
            if e.action == AuditAction.REFILL_REQUEST_QUEUE_VIEWED.value
        }
        assert viewed == {
            _PATIENT_A: {"view": "pending", "request_count": 2},
            _PATIENT_B: {"view": "pending", "request_count": 1},
        }


class TestClinicianReadsOne:
    def test_with_a_grant_and_audited(
        self,
        refill_client: TestClient,
        refill_repo: InMemoryRefillRequestRepository,
        mock_user_id: str,
        mock_audit_service: AuditService,
    ) -> None:
        _seed(refill_repo, "r1", _PATIENT_A, minutes_ago=5)
        refill_repo.grant_access(_PATIENT_A, mock_user_id)

        response = refill_client.get(f"{CLINICIAN_BASE}/r1")

        assert response.status_code == 200, response.text
        assert response.json()["medication_text"] == "Sertraline 50 mg"
        viewed = [e for e in _entries(mock_audit_service) if e.action == "refill_request_viewed"]
        assert [(e.resource_id, e.patient_id, e.actor_type) for e in viewed] == [
            ("r1", _PATIENT_A, ACTOR_TYPE_CLINICIAN)
        ]

    def test_without_a_grant_is_a_404(
        self, refill_client: TestClient, refill_repo: InMemoryRefillRequestRepository
    ) -> None:
        _seed(refill_repo, "r1", _PATIENT_A, minutes_ago=5)
        assert refill_client.get(f"{CLINICIAN_BASE}/r1").status_code == 404


class TestClinicianDecides:
    @pytest.mark.parametrize("decision", ["approved", "needs_visit", "declined"])
    def test_records_the_decision(
        self,
        refill_client: TestClient,
        refill_repo: InMemoryRefillRequestRepository,
        mock_user_id: str,
        decision: str,
    ) -> None:
        _seed(refill_repo, "r1", _PATIENT_A, minutes_ago=5)
        refill_repo.grant_access(_PATIENT_A, mock_user_id)

        response = refill_client.post(
            f"{CLINICIAN_BASE}/r1/decision",
            json={"status": decision, "prescriber_note": "sent to CVS"},
        )

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["status"] == decision
        assert body["decided_by_user_id"] == mock_user_id
        assert body["decided_at"] is not None
        assert body["prescriber_note"] == "sent to CVS"

    def test_the_patient_sees_the_decision_but_not_the_note(
        self,
        refill_client: TestClient,
        refill_repo: InMemoryRefillRequestRepository,
        mock_user_id: str,
    ) -> None:
        request_id = _ask(refill_client, medication_id=_MED_A)["id"]
        refill_repo.grant_access(_PATIENT_A, mock_user_id)
        refill_client.post(
            f"{CLINICIAN_BASE}/{request_id}/decision",
            json={"status": "needs_visit", "prescriber_note": "private"},
        )

        mine = refill_client.get(PATIENT_BASE, headers=_auth(_TOKEN_A)).json()["data"][0]
        assert mine["status"] == "needs_visit"
        assert mine["decided_at"] is not None
        assert "private" not in str(mine)

    def test_a_request_is_decided_once(
        self,
        refill_client: TestClient,
        refill_repo: InMemoryRefillRequestRepository,
        mock_user_id: str,
    ) -> None:
        _seed(refill_repo, "r1", _PATIENT_A, minutes_ago=5)
        refill_repo.grant_access(_PATIENT_A, mock_user_id)
        first = refill_client.post(f"{CLINICIAN_BASE}/r1/decision", json={"status": "approved"})
        assert first.status_code == 200

        second = refill_client.post(f"{CLINICIAN_BASE}/r1/decision", json={"status": "declined"})

        assert second.status_code == 409
        assert refill_client.get(f"{CLINICIAN_BASE}/r1").json()["status"] == "approved"

    def test_requested_is_not_a_decision(
        self,
        refill_client: TestClient,
        refill_repo: InMemoryRefillRequestRepository,
        mock_user_id: str,
    ) -> None:
        _seed(refill_repo, "r1", _PATIENT_A, minutes_ago=5)
        refill_repo.grant_access(_PATIENT_A, mock_user_id)
        response = refill_client.post(f"{CLINICIAN_BASE}/r1/decision", json={"status": "requested"})
        assert response.status_code == 422

    def test_without_a_grant_is_a_404(
        self, refill_client: TestClient, refill_repo: InMemoryRefillRequestRepository
    ) -> None:
        _seed(refill_repo, "r1", _PATIENT_A, minutes_ago=5)
        response = refill_client.post(f"{CLINICIAN_BASE}/r1/decision", json={"status": "approved"})
        assert response.status_code == 404
        assert refill_repo.list_for_patient(_PATIENT_A)[0].status == "requested"

    def test_is_audited_with_the_decision_and_no_note(
        self,
        refill_client: TestClient,
        refill_repo: InMemoryRefillRequestRepository,
        mock_user_id: str,
        mock_audit_service: AuditService,
    ) -> None:
        _seed(refill_repo, "r1", _PATIENT_A, minutes_ago=5)
        refill_repo.grant_access(_PATIENT_A, mock_user_id)
        refill_client.post(
            f"{CLINICIAN_BASE}/r1/decision",
            json={"status": "approved", "prescriber_note": "sent to CVS"},
        )

        decided = [e for e in _entries(mock_audit_service) if e.action == "refill_request_decided"]
        assert len(decided) == 1
        assert decided[0].changes == {"status": "approved", "has_note": True}
        assert decided[0].patient_id == _PATIENT_A

    def test_fires_the_decided_hook(
        self,
        refill_client: TestClient,
        refill_repo: InMemoryRefillRequestRepository,
        mock_user_id: str,
        hooks: RefillRequestHookRegistry,
    ) -> None:
        seen: list[RefillRequestEvent] = []
        hooks.register(seen.append)
        _seed(refill_repo, "r1", _PATIENT_A, minutes_ago=5)
        refill_repo.grant_access(_PATIENT_A, mock_user_id)

        refill_client.post(f"{CLINICIAN_BASE}/r1/decision", json={"status": "needs_visit"})

        assert [(e.kind, e.request_id, e.status) for e in seen] == [
            ("decided", "r1", "needs_visit")
        ]
        assert seen[0].decided_at is not None
        assert seen[0].practice_schema == "practice_test"


class TestMounting:
    def test_the_patient_router_is_mounted_only_when_configured(self) -> None:
        def paths(modules: list[str]) -> set[str]:
            return {r.path for router in portal_module_routers(modules) for r in router.routes}

        assert MODULE_MARKER_PATHS["refills"] not in paths(["intake", "messaging"])
        assert MODULE_MARKER_PATHS["refills"] in paths(["refills"])

    def test_the_clinician_router_is_always_mounted(self) -> None:
        response = TestClient(app).get(CLINICIAN_BASE)
        assert response.status_code != 404
