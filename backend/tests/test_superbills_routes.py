# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Unit tests for the superbill route (``app.routes.superbills``).

What these pin down:

* a client with claims in the period gets a PDF download with a name-free
  filename, and the audit row carries the period and the claim, line and
  charge ids — nothing off the chart;
* a refusal is 422 carrying every finding, no PDF, and an audit row naming
  the finding codes and field paths;
* an unknown or ungranted client is 404, never 403, and a backwards period
  is 422;
* with every model client patched to blow up, the route still renders.

Hermetic: every repository is in-memory and the audit service writes to an
in-memory repository so the rows can be inspected.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

import pytest
from app.auth.service import require_baa_acceptance
from app.models import User
from app.models.patient import Patient
from app.models.payments import PatientCharge
from app.repositories import (
    get_appointment_repository,
    get_claim_repository,
    get_clinician_profile_repository,
    get_patient_payment_repository,
    get_patient_repository,
    get_user_repository,
)
from app.repositories.audit import InMemoryAuditRepository
from app.repositories.claims import InMemoryClaimRepository
from app.repositories.clinician_profile import (
    ClinicianProfile,
    InMemoryClinicianProfileRepository,
)
from app.repositories.patient import InMemoryPatientRepository
from app.repositories.user import InMemoryUserRepository
from app.routes import superbills as superbill_routes
from app.scheduling_engine.repositories.appointment import InMemoryAppointmentRepository
from app.services import AuditService, get_audit_service, structured_llm_gateway, vertex_client
from fastapi import FastAPI
from fastapi.testclient import TestClient

from tests.claims_fixtures import APPOINTMENT_ID, BUILT_AT, PATIENT_ID, USER_ID, claim

_NOW = datetime(2026, 9, 6, 15, 0, tzinfo=UTC)
_DOB = "2000-01-01"
_TAX_ID = "123456789"
_URL = f"/api/patients/{PATIENT_ID}/superbill?start=2026-09-01&end=2026-09-30"


def _user(user_id: str = USER_ID) -> User:
    return User(
        id=user_id,
        email="therapist@example.com",
        name="Jane Smith",
        legal_name="Jane Smith",
        created_at=_NOW,
        baa_accepted_at=_NOW,
        baa_version="2024-01-01",
    )


class _Ledger:
    """The one read the route makes of the charge ledger."""

    def __init__(self, charges: list[PatientCharge]) -> None:
        self.charges = charges

    def list_charges(self, patient_id: str) -> list[PatientCharge]:
        return [c for c in self.charges if c.patient_id == patient_id]


@pytest.fixture
def harness() -> dict[str, Any]:
    patients = InMemoryPatientRepository()
    patients.create(
        Patient(
            id=PATIENT_ID,
            first_name="John",
            last_name="Anon",
            created_at=_NOW,
            updated_at=_NOW,
            date_of_birth=_DOB,
        ),
        USER_ID,
    )
    claims = InMemoryClaimRepository()
    claims.create(claim())
    ledger = _Ledger(
        [
            PatientCharge(
                id="charge-1",
                patient_id=PATIENT_ID,
                appointment_id=APPOINTMENT_ID,
                amount_cents=15000,
                currency="usd",
                status="succeeded",
                created_by_user_id=USER_ID,
                created_at=BUILT_AT,
            )
        ]
    )
    profiles = InMemoryClinicianProfileRepository()
    profiles.create(
        ClinicianProfile(
            user_id=USER_ID,
            practice_id="practice-1",
            npi_number="1999999984",
            license_number="LCSW-4321",
            license_state="GA",
        )
    )
    appointments = InMemoryAppointmentRepository()
    appointments.grant_access(PATIENT_ID, USER_ID)
    audit_repo = InMemoryAuditRepository()
    tax_id: dict[str, str | None] = {"value": _TAX_ID}

    app = FastAPI()
    app.include_router(superbill_routes.router)
    app.dependency_overrides[require_baa_acceptance] = _user
    app.dependency_overrides[get_patient_repository] = lambda: patients
    app.dependency_overrides[get_claim_repository] = lambda: claims
    app.dependency_overrides[get_patient_payment_repository] = lambda: ledger
    app.dependency_overrides[get_clinician_profile_repository] = lambda: profiles
    app.dependency_overrides[get_appointment_repository] = lambda: appointments
    app.dependency_overrides[get_user_repository] = InMemoryUserRepository
    app.dependency_overrides[superbill_routes.get_billing_tax_id_loader] = lambda: tax_id["value"]
    app.dependency_overrides[get_audit_service] = lambda: AuditService(audit_repo)
    client = TestClient(app, raise_server_exceptions=False)
    return {
        "app": app,
        "client": client,
        "claims": claims,
        "audit": audit_repo,
        "tax_id": tax_id,
    }


def _audit_rows(harness: dict[str, Any]) -> list[Any]:
    return [row for row in harness["audit"]._entries if row.user_id == USER_ID]


def _assert_nothing_off_the_chart(rows: list[Any]) -> None:
    text = json.dumps([row.changes for row in rows])
    assert _DOB not in text
    assert "F41" not in text
    assert "Anon" not in text
    assert "90837" not in text
    assert _TAX_ID not in text


def test_a_client_with_claims_in_the_period_gets_a_pdf(harness: dict[str, Any]) -> None:
    resp = harness["client"].get(_URL)
    assert resp.status_code == 200, resp.text
    assert resp.headers["content-type"] == "application/pdf"
    assert (
        resp.headers["content-disposition"]
        == 'attachment; filename="superbill-2026-09-01-to-2026-09-30.pdf"'
    )
    assert resp.content.startswith(b"%PDF")
    assert b"John Anon" in resp.content
    assert b"$150.00" in resp.content


def test_generation_is_audited_with_identifiers_only(harness: dict[str, Any]) -> None:
    harness["client"].get(_URL)
    rows = _audit_rows(harness)
    assert [row.action for row in rows] == ["superbill_generated"]
    row = rows[0]
    assert row.resource_type == "patient"
    assert row.resource_id == PATIENT_ID
    assert row.patient_id == PATIENT_ID
    assert row.changes == {
        "period_start": "2026-09-01",
        "period_end": "2026-09-30",
        "claim_ids": [claim().id],
        "claim_line_ids": [claim().lines[0].id],
        "charge_ids": ["charge-1"],
    }
    _assert_nothing_off_the_chart(rows)


def test_a_refusal_is_422_with_every_finding_and_no_pdf(harness: dict[str, Any]) -> None:
    harness["tax_id"]["value"] = None
    resp = harness["client"].get(_URL)
    assert resp.status_code == 422, resp.text
    assert resp.headers["content-type"].startswith("application/json")
    detail = resp.json()["detail"]
    assert detail["message"] == "The superbill is missing required information."
    assert [(f["severity"], f["code"], f["field"]) for f in detail["findings"]] == [
        ("blocking", "missing_field", "billing_provider.tax_id"),
    ]
    rows = _audit_rows(harness)
    assert [row.action for row in rows] == ["superbill_refused"]
    assert rows[0].changes == {
        "period_start": "2026-09-01",
        "period_end": "2026-09-30",
        "findings": [{"code": "missing_field", "field": "billing_provider.tax_id"}],
    }
    _assert_nothing_off_the_chart(rows)


def test_a_period_with_no_claims_is_refused(harness: dict[str, Any]) -> None:
    resp = harness["client"].get(
        f"/api/patients/{PATIENT_ID}/superbill?start=2026-10-01&end=2026-10-31"
    )
    assert resp.status_code == 422
    assert [f["code"] for f in resp.json()["detail"]["findings"]] == ["no_visits"]


def test_a_backwards_period_is_422(harness: dict[str, Any]) -> None:
    resp = harness["client"].get(
        f"/api/patients/{PATIENT_ID}/superbill?start=2026-09-30&end=2026-09-01"
    )
    assert resp.status_code == 422
    assert "before" in resp.json()["detail"]
    assert _audit_rows(harness) == []


def test_a_malformed_date_is_422(harness: dict[str, Any]) -> None:
    resp = harness["client"].get(f"/api/patients/{PATIENT_ID}/superbill?start=nope&end=2026")
    assert resp.status_code == 422


def test_an_unknown_client_is_404(harness: dict[str, Any]) -> None:
    resp = harness["client"].get("/api/patients/nope/superbill?start=2026-09-01&end=2026-09-30")
    assert resp.status_code == 404
    assert _audit_rows(harness) == []


def test_an_ungranted_client_is_404_not_403(harness: dict[str, Any]) -> None:
    harness["app"].dependency_overrides[require_baa_acceptance] = lambda: _user("other")
    resp = harness["client"].get(_URL)
    assert resp.status_code == 404


def test_the_route_reaches_no_model(
    harness: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    def forbidden(*_args: Any, **_kwargs: Any) -> Any:
        msg = "A superbill must never reach a model."
        raise AssertionError(msg)

    monkeypatch.setattr(vertex_client, "vertex_genai_client", forbidden)
    monkeypatch.setattr(vertex_client, "anthropic_vertex_client", forbidden)
    monkeypatch.setattr(structured_llm_gateway, "get_default_structured_llm_gateway", forbidden)
    monkeypatch.setattr(structured_llm_gateway, "resolve_structured_llm_gateway", forbidden)
    resp = harness["client"].get(_URL)
    assert resp.status_code == 200, resp.text
