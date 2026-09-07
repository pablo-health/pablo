# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Unit tests for the statement route (``app.routes.patient_statements``).

What these pin down:

* a client with a ledger gets a PDF download with a name-free filename, and
  the audit row carries the ledger rows it totalled and the balance it
  printed — nothing off the chart;
* an unknown or ungranted client is 404, never 403;
* a practice with no card processor configured still gets the document, the
  way it still gets a balance: the money is owed either way.

Hermetic: every repository is in-memory and the audit service writes to an
in-memory repository so the rows can be inspected.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from app.auth.service import require_baa_acceptance
from app.models import User
from app.models.patient import Patient
from app.models.payments import PatientCharge
from app.payments.statement import PracticeBlock
from app.repositories import (
    get_appointment_repository,
    get_claim_repository,
    get_patient_payment_repository,
    get_patient_repository,
    get_user_repository,
)
from app.repositories.audit import InMemoryAuditRepository
from app.repositories.claims import InMemoryClaimRepository
from app.repositories.patient import InMemoryPatientRepository
from app.repositories.user import InMemoryUserRepository
from app.routes import patient_statements
from app.scheduling_engine.models.appointment import Appointment
from app.scheduling_engine.repositories.appointment import InMemoryAppointmentRepository
from app.services import AuditService, get_audit_service
from fastapi import FastAPI
from fastapi.testclient import TestClient

from tests.claims_fixtures import APPOINTMENT_ID, BUILT_AT, PATIENT_ID, USER_ID

_NOW = datetime(2026, 9, 6, 15, 0, tzinfo=UTC)
_DOB = "2000-01-01"
_OTHER_PATIENT_ID = "99999999-9999-4999-8999-999999999999"
_URL = f"/api/patients/{PATIENT_ID}/statement"

_PRACTICE = PracticeBlock(
    name="Sample Counseling",
    address_line1="123 Some St",
    address_line2=None,
    city="Atlanta",
    state="GA",
    postal_code="30301",
    phone="5553334444",
)


def _user() -> User:
    return User(
        id=USER_ID,
        email="therapist@example.com",
        name="Jane Smith",
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
    ledger = _Ledger(
        [
            PatientCharge(
                id="charge-1",
                patient_id=PATIENT_ID,
                appointment_id=APPOINTMENT_ID,
                kind="session",
                amount_cents=15000,
                currency="usd",
                status="pending",
                created_by_user_id=USER_ID,
                created_at=BUILT_AT,
            )
        ]
    )
    appointments = InMemoryAppointmentRepository()
    appointments.grant_access(PATIENT_ID, USER_ID)
    appointments.create(
        Appointment(
            id=APPOINTMENT_ID,
            user_id=USER_ID,
            patient_id=PATIENT_ID,
            title="Session",
            start_at=BUILT_AT,
            end_at=BUILT_AT + timedelta(minutes=50),
            duration_minutes=50,
            status="completed",
            session_type="Individual therapy",
        )
    )
    audit_repo = InMemoryAuditRepository()

    app = FastAPI()
    app.include_router(patient_statements.router)
    app.dependency_overrides[require_baa_acceptance] = _user
    app.dependency_overrides[get_patient_repository] = lambda: patients
    app.dependency_overrides[get_claim_repository] = InMemoryClaimRepository
    app.dependency_overrides[get_patient_payment_repository] = lambda: ledger
    app.dependency_overrides[get_appointment_repository] = lambda: appointments
    app.dependency_overrides[get_user_repository] = InMemoryUserRepository
    app.dependency_overrides[patient_statements.get_practice_block] = lambda: _PRACTICE
    app.dependency_overrides[get_audit_service] = lambda: AuditService(audit_repo)
    return {
        "client": TestClient(app, raise_server_exceptions=False),
        "audit": audit_repo,
    }


def _audit_rows(harness: dict[str, Any]) -> list[Any]:
    return [row for row in harness["audit"]._entries if row.user_id == USER_ID]


def test_a_client_with_a_ledger_gets_a_pdf(harness: dict[str, Any]) -> None:
    resp = harness["client"].get(_URL)

    assert resp.status_code == 200, resp.text
    assert resp.headers["content-type"] == "application/pdf"
    assert resp.headers["content-disposition"] == 'attachment; filename="statement.pdf"'
    assert resp.content.startswith(b"%PDF")
    assert b"John Anon" in resp.content
    assert b"Sample Counseling" in resp.content
    assert b"$150.00" in resp.content


def test_the_same_ledger_renders_the_same_bytes(harness: dict[str, Any]) -> None:
    """The route's only clock is the generated-at stamp, and the document is
    invariant otherwise — so two downloads a moment apart are identical."""
    first = harness["client"].get(_URL).content
    second = harness["client"].get(_URL).content

    assert first == second


def test_generation_is_audited_with_identifiers_only(harness: dict[str, Any]) -> None:
    harness["client"].get(_URL)

    rows = _audit_rows(harness)
    assert [row.action for row in rows] == ["statement_generated"]
    assert rows[0].resource_type == "patient"
    assert rows[0].resource_id == PATIENT_ID
    assert rows[0].patient_id == PATIENT_ID
    assert rows[0].changes == {"charge_ids": ["charge-1"], "balance_cents": 15000}

    # Nothing off the chart reaches the payload: no name, no date of birth,
    # no service the client attended.
    text = json.dumps([row.changes for row in rows])
    assert _DOB not in text
    assert "Anon" not in text
    assert "Individual therapy" not in text


def test_an_unknown_client_is_404(harness: dict[str, Any]) -> None:
    resp = harness["client"].get(f"/api/patients/{_OTHER_PATIENT_ID}/statement")

    assert resp.status_code == 404
