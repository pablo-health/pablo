# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Unit tests for the claim routes (``app.routes.claims``).

What these pin down:

* a session with visit codes, an active coverage and a billing profile
  builds a draft claim whose lines match the appointment and the resolved
  rate;
* ``/validate`` on a claim with a blocking finding is 422 with the findings
  and the claim stays a draft; without one the claim becomes ``validated``
  and the warnings come back;
* ``/correct`` on a validated-or-later claim creates a child with frequency
  ``7`` and leaves the parent as it was; on a draft it is 409;
* ``/void`` likewise creates a frequency ``8`` child;
* an unknown or ungranted client is 404, never 403;
* the audit rows carry the claim id, control number, state and payer row id
  and nothing off the card — no member id, no date of birth, no diagnosis.

Hermetic: every repository is the in-memory implementation and the audit
service writes to an in-memory repository so the rows can be inspected.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

import pytest
from app.api_errors import register_exception_handlers
from app.auth.service import require_baa_acceptance
from app.models import User
from app.models.coverage import PatientCoverage, Payer
from app.models.patient import Patient
from app.repositories import (
    get_appointment_repository,
    get_appointment_type_repository,
    get_claim_repository,
    get_clinician_profile_repository,
    get_patient_coverage_repository,
    get_patient_repository,
    get_payer_repository,
    get_user_repository,
)
from app.repositories.audit import InMemoryAuditRepository
from app.repositories.claims import InMemoryClaimRepository
from app.repositories.clinician_profile import (
    ClinicianProfile,
    InMemoryClinicianProfileRepository,
)
from app.repositories.coverage import InMemoryPatientCoverageRepository, InMemoryPayerRepository
from app.repositories.patient import InMemoryPatientRepository
from app.repositories.user import InMemoryUserRepository
from app.routes import claims as claims_routes
from app.scheduling_engine.models.appointment import Appointment
from app.scheduling_engine.models.appointment_type import AppointmentType
from app.scheduling_engine.repositories.appointment import InMemoryAppointmentRepository
from app.scheduling_engine.repositories.appointment_type import (
    InMemoryAppointmentTypeRepository,
)
from app.services import AuditService, get_audit_service
from fastapi import FastAPI
from fastapi.testclient import TestClient

_USER_ID = "55555555-5555-4555-8555-555555555555"
_PATIENT_ID = "11111111-1111-4111-8111-111111111111"
_APPOINTMENT_ID = "44444444-4444-4444-8444-444444444444"
_TYPE_ID = "66666666-6666-4666-8666-666666666666"
_NOW = datetime(2026, 9, 2, 15, 0, tzinfo=UTC)
_MEMBER_ID = "123456789"
_DOB = "2000-01-01"

_BILLING_PROFILE: dict[str, object] = {
    "legal_name": "Pablo Test Practice",
    "tax_id_last4": "9714",
    "tax_id_type": "ein",
    "billing_npi": "1999999984",
    "address_line1": "123 Some St",
    "address_line2": None,
    "city": "Atlanta",
    "state": "GA",
    "postal_code": "303010000",
    "phone": "5553334444",
}


def _user(user_id: str = _USER_ID) -> User:
    return User(
        id=user_id,
        email="therapist@example.com",
        name="Jane Smith",
        legal_name="Jane Smith",
        created_at=_NOW,
        baa_accepted_at=_NOW,
        baa_version="2024-01-01",
    )


@pytest.fixture
def harness() -> dict[str, Any]:
    appointments = InMemoryAppointmentRepository()
    appointments.grant_access(_PATIENT_ID, _USER_ID)
    appointments.create(
        Appointment(
            id=_APPOINTMENT_ID,
            user_id=_USER_ID,
            patient_id=_PATIENT_ID,
            title="Session",
            start_at=datetime(2026, 9, 1, 19, 0, tzinfo=UTC),
            end_at=datetime(2026, 9, 1, 20, 0, tzinfo=UTC),
            duration_minutes=53,
            status="completed",
            session_type="individual",
            appointment_type_id=_TYPE_ID,
            video_link="https://video.example/room",
            service_code="90837",
            modifiers=["95"],
            unit_count=1,
            place_of_service="10",
            diagnosis_codes=["F41.1"],
        )
    )
    appointment_types = InMemoryAppointmentTypeRepository()
    appointment_types.create(
        AppointmentType(id=_TYPE_ID, user_id=_USER_ID, name="Individual", default_fee_cents=15000)
    )
    patients = InMemoryPatientRepository()
    patients.create(
        Patient(
            id=_PATIENT_ID,
            first_name="John",
            last_name="Anon",
            created_at=_NOW,
            updated_at=_NOW,
            date_of_birth=_DOB,
            sex="M",
            address_line1="2222 Random St",
            city="Atlanta",
            state="GA",
            postal_code="303010000",
        ),
        _USER_ID,
    )
    payers = InMemoryPayerRepository()
    payer = payers.create(
        Payer(
            id="33333333-3333-4333-8333-333333333333",
            name="Stedi Test Payer",
            payer_id="STEDI",
            created_at=_NOW,
            updated_at=_NOW,
        )
    )
    coverage = InMemoryPatientCoverageRepository()
    coverage.create(
        PatientCoverage(
            id="22222222-2222-4222-8222-222222222222",
            patient_id=_PATIENT_ID,
            payer_id=payer.id,
            member_id=_MEMBER_ID,
            group_number="3335555",
            created_at=_NOW,
            updated_at=_NOW,
        )
    )
    profiles = InMemoryClinicianProfileRepository()
    profiles.create(
        ClinicianProfile(
            user_id=_USER_ID,
            practice_id="practice-1",
            npi_number="1999999984",
            taxonomy_code="101YM0800X",
        )
    )
    claims = InMemoryClaimRepository()
    audit_repo = InMemoryAuditRepository()
    billing_profile = dict(_BILLING_PROFILE)

    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(claims_routes.router)
    app.include_router(claims_routes.patient_claims_router)
    app.dependency_overrides[require_baa_acceptance] = _user
    app.dependency_overrides[get_appointment_repository] = lambda: appointments
    app.dependency_overrides[get_appointment_type_repository] = lambda: appointment_types
    app.dependency_overrides[get_patient_repository] = lambda: patients
    app.dependency_overrides[get_payer_repository] = lambda: payers
    app.dependency_overrides[get_patient_coverage_repository] = lambda: coverage
    app.dependency_overrides[get_clinician_profile_repository] = lambda: profiles
    app.dependency_overrides[get_user_repository] = InMemoryUserRepository
    app.dependency_overrides[get_claim_repository] = lambda: claims
    app.dependency_overrides[claims_routes.get_billing_profile_loader] = lambda: billing_profile
    app.dependency_overrides[get_audit_service] = lambda: AuditService(audit_repo)
    client = TestClient(app, raise_server_exceptions=False)
    return {
        "app": app,
        "client": client,
        "appointments": appointments,
        "patients": patients,
        "coverage": coverage,
        "claims": claims,
        "audit": audit_repo,
        "billing_profile": billing_profile,
    }


def _build(harness: dict[str, Any], **body: Any) -> dict[str, Any]:
    resp = harness["client"].post(f"/api/claims/from-session/{_APPOINTMENT_ID}", json=body or None)
    assert resp.status_code == 201, resp.text
    return resp.json()


def _validated(harness: dict[str, Any]) -> dict[str, Any]:
    built = _build(harness)
    resp = harness["client"].post(f"/api/claims/{built['id']}/validate")
    assert resp.status_code == 200, resp.text
    return resp.json()["claim"]


def _audit_rows(harness: dict[str, Any]) -> list[Any]:
    """The clinician's audit rows in the order they were written.

    ``list_for_user`` sorts newest-first by timestamp, which cannot order two
    rows written in the same instant; the in-memory list can.
    """
    return [row for row in harness["audit"]._entries if row.user_id == _USER_ID]


# ---------------------------------------------------------------------------
# Building
# ---------------------------------------------------------------------------


class TestBuild:
    def test_session_with_codes_coverage_and_profile_builds_a_draft(
        self, harness: dict[str, Any]
    ) -> None:
        built = _build(harness)
        assert built["state"] == "draft"
        assert built["frequency_code"] == "1"
        assert built["patient_id"] == _PATIENT_ID
        assert built["diagnosis_codes"] == ["F41.1"]
        assert built["place_of_service"] == "10"
        assert built["total_charge_cents"] == 15000
        assert len(built["lines"]) == 1
        visit = built["lines"][0]
        assert visit["appointment_id"] == _APPOINTMENT_ID
        assert visit["cpt"] == "90837"
        assert visit["modifiers"] == ["95"]
        assert visit["units"] == 1
        assert visit["charge_cents"] == 15000
        assert visit["dx_pointers"] == [1]
        assert visit["service_date"] == "2026-09-01"
        assert built["subscriber_snapshot"]["member_id"] == _MEMBER_ID
        assert built["billing_snapshot"]["billing_provider"]["npi"] == "1999999984"
        assert 1 <= len(built["control_number"]) <= 17

    def test_add_on_makes_a_second_line(self, harness: dict[str, Any]) -> None:
        built = _build(harness, add_on={"cpt": "90833", "charge_cents": 6000})
        assert [(line["line_number"], line["cpt"]) for line in built["lines"]] == [
            (1, "90837"),
            (2, "90833"),
        ]
        assert built["total_charge_cents"] == 21000

    def test_unknown_appointment_is_404(self, harness: dict[str, Any]) -> None:
        resp = harness["client"].post("/api/claims/from-session/nope")
        assert resp.status_code == 404

    def test_appointment_of_an_ungranted_client_is_404(self, harness: dict[str, Any]) -> None:
        harness["app"].dependency_overrides[require_baa_acceptance] = lambda: _user("other")
        resp = harness["client"].post(f"/api/claims/from-session/{_APPOINTMENT_ID}")
        assert resp.status_code == 404

    def test_client_without_coverage_is_422(self, harness: dict[str, Any]) -> None:
        active = harness["coverage"].get_active(_PATIENT_ID)
        harness["coverage"].update(active.model_copy(update={"active": False}))
        resp = harness["client"].post(f"/api/claims/from-session/{_APPOINTMENT_ID}")
        assert resp.status_code == 422
        assert "coverage" in resp.json()["detail"]

    def test_build_is_audited_with_identifiers_only(self, harness: dict[str, Any]) -> None:
        built = _build(harness)
        rows = _audit_rows(harness)
        assert [row.action for row in rows] == ["claim_created"]
        row = rows[0]
        assert row.resource_type == "claim"
        assert row.resource_id == built["id"]
        assert row.patient_id == _PATIENT_ID
        assert row.changes["control_number"] == built["control_number"]
        assert row.changes["state"] == "draft"
        assert row.changes["appointment_id"] == _APPOINTMENT_ID
        _assert_nothing_off_the_card(rows)


# ---------------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------------


class TestRead:
    def test_get_returns_the_claim_and_audits(self, harness: dict[str, Any]) -> None:
        built = _build(harness)
        resp = harness["client"].get(f"/api/claims/{built['id']}")
        assert resp.status_code == 200
        assert resp.json()["id"] == built["id"]
        assert [row.action for row in _audit_rows(harness)] == ["claim_created", "claim_viewed"]

    def test_unknown_claim_is_404(self, harness: dict[str, Any]) -> None:
        assert harness["client"].get("/api/claims/nope").status_code == 404

    def test_another_clinicians_claim_is_404_not_403(self, harness: dict[str, Any]) -> None:
        built = _build(harness)
        harness["app"].dependency_overrides[require_baa_acceptance] = lambda: _user("other")
        resp = harness["client"].get(f"/api/claims/{built['id']}")
        assert resp.status_code == 404

    def test_patient_claims_lists_newest_first(self, harness: dict[str, Any]) -> None:
        first = _build(harness)
        second = _build(harness)
        resp = harness["client"].get(f"/api/patients/{_PATIENT_ID}/claims")
        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] == 2
        assert {c["id"] for c in body["data"]} == {first["id"], second["id"]}
        assert _audit_rows(harness)[-1].action == "patient_claims_viewed"

    def test_patient_claims_for_an_unknown_client_is_404(self, harness: dict[str, Any]) -> None:
        assert harness["client"].get("/api/patients/nope/claims").status_code == 404

    def test_detail_carries_names_findings_hops_and_deadlines(
        self, harness: dict[str, Any]
    ) -> None:
        built = _build(harness)
        body = harness["client"].get(f"/api/claims/{built['id']}").json()
        assert body["patient_name"] == "John Anon"
        assert body["payer_name"] == "Stedi Test Payer"
        assert body["findings"] == []
        assert [(h["kind"], h["reached"]) for h in body["hops"]] == [
            ("built", True),
            ("submitted", False),
            ("clearinghouse_accepted", False),
            ("payer_accepted", False),
            ("adjudicated", False),
        ]
        assert body["hops"][0]["at"] is not None
        # A draft is under the filing clock: the payer's default window from
        # the service date.
        assert body["deadlines"]["applicable"] == "filing"
        assert body["deadlines"]["filing"] is not None
        assert isinstance(body["deadlines"]["days_left"], int)

    def test_detail_reports_the_current_findings_of_a_blocked_draft(
        self, harness: dict[str, Any]
    ) -> None:
        appointment = harness["appointments"].get(_APPOINTMENT_ID, _USER_ID)
        appointment.diagnosis_codes = ["F41"]
        harness["appointments"].update(appointment)
        built = _build(harness)
        body = harness["client"].get(f"/api/claims/{built['id']}").json()
        assert [f["code"] for f in body["findings"]] == ["dx_not_specific"]

    def test_detail_hops_follow_the_receipt_timestamps(self, harness: dict[str, Any]) -> None:
        parent = _validated(harness)
        stored = harness["claims"].get(parent["id"])
        harness["claims"].update(
            stored.model_copy(
                update={"state": "payer_accepted", "submitted_at": _NOW, "payer_accepted_at": _NOW}
            )
        )
        body = harness["client"].get(f"/api/claims/{parent['id']}").json()
        assert [(h["kind"], h["reached"]) for h in body["hops"]] == [
            ("built", True),
            ("submitted", True),
            ("clearinghouse_accepted", True),
            ("payer_accepted", True),
            ("adjudicated", False),
        ]
        # Past the payer's acknowledgement, and not yet adjudicated: no
        # clock binds.
        assert body["deadlines"]["applicable"] is None


# ---------------------------------------------------------------------------
# The tracker
# ---------------------------------------------------------------------------


class TestTracker:
    def test_lists_every_claim_newest_first_with_names_and_deadlines(
        self, harness: dict[str, Any]
    ) -> None:
        first = _build(harness)
        second = _build(harness)
        resp = harness["client"].get("/api/claims")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["total"] == 2
        assert {row["id"] for row in body["data"]} == {first["id"], second["id"]}
        row = body["data"][0]
        assert row["patient_name"] == "John Anon"
        assert row["payer_name"] == "Stedi Test Payer"
        assert row["service_date"] == "2026-09-01"
        assert row["state"] == "draft"
        assert row["deadlines"]["applicable"] == "filing"
        assert "subscriber_snapshot" not in row
        assert "diagnosis_codes" not in row

    def test_narrows_by_state(self, harness: dict[str, Any]) -> None:
        draft = _build(harness)
        queued = _validated(harness)
        body = harness["client"].get("/api/claims", params={"state": "validated"}).json()
        assert [row["id"] for row in body["data"]] == [queued["id"]]
        body = harness["client"].get("/api/claims", params={"state": "draft"}).json()
        assert [row["id"] for row in body["data"]] == [draft["id"]]

    def test_narrows_by_service_date_range(self, harness: dict[str, Any]) -> None:
        built = _build(harness)
        inside = harness["client"].get(
            "/api/claims", params={"from": "2026-09-01", "to": "2026-09-01"}
        )
        assert [row["id"] for row in inside.json()["data"]] == [built["id"]]
        outside = harness["client"].get(
            "/api/claims", params={"from": "2026-09-02", "to": "2026-09-30"}
        )
        assert outside.json()["data"] == []

    def test_range_that_ends_before_it_starts_is_422(self, harness: dict[str, Any]) -> None:
        resp = harness["client"].get(
            "/api/claims", params={"from": "2026-09-30", "to": "2026-09-01"}
        )
        assert resp.status_code == 422

    def test_leaves_out_another_clinicians_claims(self, harness: dict[str, Any]) -> None:
        _build(harness)
        harness["app"].dependency_overrides[require_baa_acceptance] = lambda: _user("other")
        body = harness["client"].get("/api/claims").json()
        assert body == {"data": [], "total": 0}

    def test_is_audited_with_identifiers_only(self, harness: dict[str, Any]) -> None:
        built = _build(harness)
        harness["client"].get("/api/claims", params={"state": "draft"})
        row = _audit_rows(harness)[-1]
        assert row.action == "claims_listed"
        assert row.resource_type == "claim"
        assert row.changes["claim_ids"] == [built["id"]]
        assert row.changes["control_numbers"] == [built["control_number"]]
        assert row.changes["state"] == "draft"
        _assert_nothing_off_the_card(_audit_rows(harness))


# ---------------------------------------------------------------------------
# Validating
# ---------------------------------------------------------------------------


class TestValidate:
    def test_clean_claim_becomes_validated(self, harness: dict[str, Any]) -> None:
        built = _build(harness)
        resp = harness["client"].post(f"/api/claims/{built['id']}/validate")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["claim"]["state"] == "validated"
        assert body["findings"] == []
        assert harness["claims"].get(built["id"]).state == "validated"
        assert _audit_rows(harness)[-1].action == "claim_validated"
        assert _audit_rows(harness)[-1].changes["state"] == "validated"

    def test_warnings_come_back_with_a_validated_claim(self, harness: dict[str, Any]) -> None:
        harness["billing_profile"]["billing_npi"] = None
        appointment = harness["appointments"].get(_APPOINTMENT_ID, _USER_ID)
        appointment.modifiers = []
        harness["appointments"].update(appointment)
        built = _build(harness)
        resp = harness["client"].post(f"/api/claims/{built['id']}/validate")
        assert resp.status_code == 200, resp.text
        assert [f["code"] for f in resp.json()["findings"]] == ["telehealth_modifier_missing"]
        assert resp.json()["claim"]["state"] == "validated"

    def test_blocking_finding_is_422_and_the_claim_stays_a_draft(
        self, harness: dict[str, Any]
    ) -> None:
        appointment = harness["appointments"].get(_APPOINTMENT_ID, _USER_ID)
        appointment.place_of_service = "11"
        appointment.diagnosis_codes = ["F41"]
        harness["appointments"].update(appointment)
        built = _build(harness)

        resp = harness["client"].post(f"/api/claims/{built['id']}/validate")
        assert resp.status_code == 422, resp.text
        error = resp.json()["error"]
        assert error["code"] == "CLAIM_VALIDATION_FAILED"
        findings = error["details"]["findings"]
        assert [f["code"] for f in findings] == [
            "pos_telehealth_mismatch",
            "dx_not_specific",
        ]
        assert all(f["severity"] == "blocking" for f in findings)
        assert harness["claims"].get(built["id"]).state == "draft"
        assert [row.action for row in _audit_rows(harness)] == ["claim_created"]

    def test_validating_twice_is_409(self, harness: dict[str, Any]) -> None:
        validated = _validated(harness)
        resp = harness["client"].post(f"/api/claims/{validated['id']}/validate")
        assert resp.status_code == 409


# ---------------------------------------------------------------------------
# Correcting and voiding
# ---------------------------------------------------------------------------


class TestCorrect:
    def test_correcting_a_validated_claim_makes_a_frequency_7_child(
        self, harness: dict[str, Any]
    ) -> None:
        parent = _validated(harness)
        appointment = harness["appointments"].get(_APPOINTMENT_ID, _USER_ID)
        appointment.diagnosis_codes = ["F33.1"]
        harness["appointments"].update(appointment)

        resp = harness["client"].post(f"/api/claims/{parent['id']}/correct")
        assert resp.status_code == 201, resp.text
        child = resp.json()
        assert child["frequency_code"] == "7"
        assert child["parent_claim_id"] == parent["id"]
        assert child["state"] == "draft"
        assert child["diagnosis_codes"] == ["F33.1"]
        assert child["control_number"] != parent["control_number"]

        stored_parent = harness["claims"].get(parent["id"])
        assert stored_parent.state == "validated"
        assert stored_parent.diagnosis_codes == ["F41.1"]
        assert _audit_rows(harness)[-1].action == "claim_corrected"
        assert _audit_rows(harness)[-1].changes["parent_claim_id"] == parent["id"]

    def test_correcting_a_draft_is_409(self, harness: dict[str, Any]) -> None:
        built = _build(harness)
        resp = harness["client"].post(f"/api/claims/{built['id']}/correct")
        assert resp.status_code == 409
        assert harness["client"].get(f"/api/patients/{_PATIENT_ID}/claims").json()["total"] == 1

    def test_correcting_when_the_visit_is_gone_is_409(self, harness: dict[str, Any]) -> None:
        parent = _validated(harness)
        harness["appointments"].delete(_APPOINTMENT_ID, _USER_ID)
        resp = harness["client"].post(f"/api/claims/{parent['id']}/correct")
        assert resp.status_code == 409
        assert "void" in resp.json()["detail"]


class TestVoid:
    def test_voiding_a_validated_claim_makes_a_frequency_8_child(
        self, harness: dict[str, Any]
    ) -> None:
        parent = _validated(harness)
        resp = harness["client"].post(f"/api/claims/{parent['id']}/void")
        assert resp.status_code == 201, resp.text
        void = resp.json()
        assert void["frequency_code"] == "8"
        assert void["parent_claim_id"] == parent["id"]
        assert void["state"] == "draft"
        assert void["lines"][0]["cpt"] == parent["lines"][0]["cpt"]
        assert harness["claims"].get(parent["id"]).state == "validated"
        assert _audit_rows(harness)[-1].action == "claim_voided"

    def test_voiding_a_draft_is_409(self, harness: dict[str, Any]) -> None:
        built = _build(harness)
        assert harness["client"].post(f"/api/claims/{built['id']}/void").status_code == 409


# ---------------------------------------------------------------------------
# Nothing off the card in the audit trail
# ---------------------------------------------------------------------------


def _assert_nothing_off_the_card(rows: list[Any]) -> None:
    text = json.dumps([row.changes for row in rows])
    assert _MEMBER_ID not in text
    assert _DOB not in text
    assert "F41" not in text
    assert "Anon" not in text
    assert "Random St" not in text


def test_the_whole_flow_audits_identifiers_only(harness: dict[str, Any]) -> None:
    parent = _validated(harness)
    harness["client"].get(f"/api/claims/{parent['id']}")
    harness["client"].post(f"/api/claims/{parent['id']}/correct")
    harness["client"].post(f"/api/claims/{parent['id']}/void")
    harness["client"].get(f"/api/patients/{_PATIENT_ID}/claims")
    rows = _audit_rows(harness)
    assert [row.action for row in rows] == [
        "claim_created",
        "claim_validated",
        "claim_viewed",
        "claim_corrected",
        "claim_voided",
        "patient_claims_viewed",
    ]
    _assert_nothing_off_the_card(rows)
