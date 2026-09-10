# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""The enrollment surface on ``/api/payers`` and the coverage-save trigger.

* ``POST /api/payers/{id}/enrollments`` files what the payer needs and
  answers with the full set; each way it cannot has its own status.
* ``GET /api/payers/{id}/enrollments`` lists what is on file.
* Putting a plan on file for a payer calls the enrollment trigger; editing
  a plan calls it only when the payer changed.

The payer routes run on an in-memory SQLite session (the enrollment flow
reads and writes ORM rows directly) with the clearinghouse answered from
recorded fixtures; the coverage routes keep their in-memory repositories
and swap the trigger for a recorder.
"""

from __future__ import annotations

import base64
import os
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import pytest
from app.auth.service import (
    TenantContext,
    get_tenant_context,
    require_active_subscription,
    require_baa_acceptance,
)
from app.claims import enrollment
from app.claims.eligibility import EligibilityAutoCheck, get_eligibility_auto_check
from app.db import get_db_session
from app.db.models import (
    ComplianceItemRow,
    PayerEnrollmentRow,
    PayerRow,
    PracticeBillingProfileRow,
)
from app.models import User
from app.models.patient import Patient
from app.repositories import (
    get_patient_coverage_repository,
    get_patient_repository,
    get_payer_repository,
)
from app.repositories.audit import InMemoryAuditRepository
from app.repositories.coverage import InMemoryPatientCoverageRepository, InMemoryPayerRepository
from app.repositories.postgres.coverage import PostgresPayerRepository
from app.routes import coverage as coverage_routes
from app.services import AuditService, get_audit_service
from app.services.coverage_intake import new_payer
from app.services.practice_billing_profile import update_billing_profile
from app.settings import get_settings
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from tests.enrollment_fakes import TEST_PAYER_ID, FakeClearinghouse, enrollment_fixture
from tests.sqlite_engine import sqlite_engine

if TYPE_CHECKING:
    from collections.abc import Iterator

    from sqlalchemy import Engine

_USER_ID = "11111111-1111-4111-8111-111111111111"
_PATIENT_ID = "44444444-4444-4444-8444-444444444444"

_PROFILE = {
    "legal_name": "Pablo Health Test Provider",
    "tax_id": "84-4459714",
    "tax_id_type": "ein",
    "billing_npi": "1999999984",
    "address_line1": "1 Test St",
    "city": "Atlanta",
    "state": "GA",
    "postal_code": "30301",
    "phone": "4045550100",
    "contact_email": "billing@example.com",
}


def _user() -> User:
    return User(
        id=_USER_ID,
        email="therapist@example.com",
        name="Test Therapist",
        created_at=datetime.now(UTC),
        baa_accepted_at=datetime.now(UTC),
        baa_version="2024-01-01",
    )


def _tenant() -> TenantContext:
    return TenantContext(user_id=_USER_ID, practice_id="practice-1", practice_schema="practice_x")


@pytest.fixture(autouse=True)
def _encryption_key(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("GOOGLE_CALENDAR_ENCRYPTION_KEY", base64.b64encode(os.urandom(32)).decode())
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


# --- the payer endpoints, over SQLite ------------------------------------------


@pytest.fixture
def engine() -> Iterator[Engine]:
    with sqlite_engine(
        [
            PracticeBillingProfileRow.__table__,
            PayerRow.__table__,
            PayerEnrollmentRow.__table__,
            ComplianceItemRow.__table__,
        ]
    ) as eng:
        yield eng


@pytest.fixture
def clearinghouse() -> Iterator[FakeClearinghouse]:
    client = FakeClearinghouse()
    enrollment.register_clearinghouse_client_factory(lambda _practice_id: client)
    yield client
    enrollment.register_clearinghouse_client_factory(None)


@pytest.fixture
def payer_harness(engine: Engine, clearinghouse: FakeClearinghouse) -> Iterator[dict[str, Any]]:
    session = Session(engine)
    payers = PostgresPayerRepository(session)
    payer = payers.create(new_payer(name="Stedi Test Payer", payer_id=TEST_PAYER_ID))

    app = FastAPI()
    app.include_router(coverage_routes.payers_router)
    app.dependency_overrides[require_active_subscription] = _user
    app.dependency_overrides[get_tenant_context] = _tenant
    app.dependency_overrides[get_payer_repository] = lambda: payers
    app.dependency_overrides[get_db_session] = lambda: session
    client = TestClient(app, raise_server_exceptions=False)
    try:
        yield {"client": client, "session": session, "payer": payer, "clearinghouse": clearinghouse}
    finally:
        session.close()


def _complete_profile(session: Session, **overrides: str | None) -> None:
    update_billing_profile(session, {**_PROFILE, **overrides})
    session.flush()


class TestRequestEnrollments:
    def test_files_and_answers_with_the_set(self, payer_harness: dict[str, Any]) -> None:
        _complete_profile(payer_harness["session"])
        payer = payer_harness["payer"]

        response = payer_harness["client"].post(f"/api/payers/{payer.id}/enrollments")

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["enrollment_status"] == "filed"
        [request] = body["data"]
        assert request["transaction_type"] == "835"
        assert request["status"] == "stedi_action_required"
        assert request["vendor_request_id"] == "enr-0001"
        assert request["instructions"] is None

    def test_pressing_twice_files_nothing_new(self, payer_harness: dict[str, Any]) -> None:
        _complete_profile(payer_harness["session"])
        payer = payer_harness["payer"]
        client = payer_harness["client"]

        client.post(f"/api/payers/{payer.id}/enrollments")
        response = client.post(f"/api/payers/{payer.id}/enrollments")

        assert response.status_code == 200
        assert len(response.json()["data"]) == 1
        assert len(payer_harness["clearinghouse"].calls_named("create_enrollment")) == 1

    def test_incomplete_profile_is_422_naming_the_fields(
        self, payer_harness: dict[str, Any]
    ) -> None:
        _complete_profile(payer_harness["session"], contact_email=None)
        payer = payer_harness["payer"]

        response = payer_harness["client"].post(f"/api/payers/{payer.id}/enrollments")

        assert response.status_code == 422
        assert "contact_email" in response.json()["detail"]

    def test_no_clearinghouse_is_503(self, payer_harness: dict[str, Any]) -> None:
        enrollment.register_clearinghouse_client_factory(lambda _practice_id: None)
        _complete_profile(payer_harness["session"])
        payer = payer_harness["payer"]

        response = payer_harness["client"].post(f"/api/payers/{payer.id}/enrollments")

        assert response.status_code == 503

    def test_unknown_payer_row_is_404(self, payer_harness: dict[str, Any]) -> None:
        response = payer_harness["client"].post("/api/payers/nope/enrollments")

        assert response.status_code == 404


class TestListEnrollments:
    def test_empty_before_anything_is_filed(self, payer_harness: dict[str, Any]) -> None:
        payer = payer_harness["payer"]

        response = payer_harness["client"].get(f"/api/payers/{payer.id}/enrollments")

        assert response.status_code == 200
        assert response.json() == {"data": [], "enrollment_status": "none"}

    def test_lists_what_was_filed(self, payer_harness: dict[str, Any]) -> None:
        _complete_profile(payer_harness["session"])
        payer = payer_harness["payer"]
        client = payer_harness["client"]
        client.post(f"/api/payers/{payer.id}/enrollments")

        response = client.get(f"/api/payers/{payer.id}/enrollments")

        assert [r["transaction_type"] for r in response.json()["data"]] == ["835"]


_DOCUMENT_TASK_ID = "01a0a1b2-3c4d-7e5f-8a6b-7c8d9e0f1a2b"
_EMPTY_TASK_ID = "01a0a1b2-3c4d-7e5f-8a6b-7c8d9e0f1a2c"


def _file_enrollment(payer_harness: dict[str, Any]) -> str:
    """File the payer's 835 enrollment and return its vendor request id."""
    _complete_profile(payer_harness["session"])
    payer = payer_harness["payer"]
    response = payer_harness["client"].post(f"/api/payers/{payer.id}/enrollments")
    vendor_request_id: str = response.json()["data"][0]["vendor_request_id"]
    return vendor_request_id


class TestEnrollmentTaskDetail:
    def test_renders_fields_and_tells_link_kinds_apart(self, payer_harness: dict[str, Any]) -> None:
        vendor_request_id = _file_enrollment(payer_harness)
        data = enrollment_fixture(vendor_id=vendor_request_id, status="PROVIDER_ACTION_REQUIRED")
        data["tasks"][0]["definition"]["manualTask"]["links"].append(
            {
                "label": "Stedi-hosted template",
                "url": "https://enrollments.us.stedi.com/2024-09-01/documents/doc-9",
            }
        )
        payer_harness["clearinghouse"].listing = [data]
        payer = payer_harness["payer"]

        response = payer_harness["client"].get(f"/api/payers/{payer.id}/enrollments/835/detail")

        assert response.status_code == 200, response.text
        body = response.json()
        [document_task, stedi_task] = body["tasks"]
        assert document_task["id"] == _DOCUMENT_TASK_ID
        [field] = document_task["fields"]
        assert field == {
            "key": "signed_eft_form",
            "label": "Signed EFT authorization",
            "description": None,
            "field_type": "DOCUMENT",
        }
        kinds = {link["url"]: link["kind"] for link in document_task["links"]}
        assert kinds["https://example.com/payer/eft-authorization.pdf"] == "external"
        assert kinds["https://enrollments.us.stedi.com/2024-09-01/documents/doc-9"] == (
            "stedi_document"
        )
        assert stedi_task["fields"] == []

    def test_unknown_transaction_is_404(self, payer_harness: dict[str, Any]) -> None:
        payer = payer_harness["payer"]

        response = payer_harness["client"].get(f"/api/payers/{payer.id}/enrollments/835/detail")

        assert response.status_code == 404


class TestResolveEnrollmentTaskLink:
    def test_resolves_a_stedi_document_link(self, payer_harness: dict[str, Any]) -> None:
        vendor_request_id = _file_enrollment(payer_harness)
        data = enrollment_fixture(vendor_id=vendor_request_id, status="PROVIDER_ACTION_REQUIRED")
        data["tasks"][0]["definition"]["manualTask"]["links"][0]["url"] = (
            "https://enrollments.us.stedi.com/2024-09-01/documents/doc-9"
        )
        payer_harness["clearinghouse"].listing = [data]
        payer = payer_harness["payer"]

        response = payer_harness["client"].get(
            f"/api/payers/{payer.id}/enrollments/835/tasks/{_DOCUMENT_TASK_ID}/links/0"
        )

        assert response.status_code == 200, response.text
        assert response.json()["url"].endswith("?presigned=1")

    def test_an_external_link_is_not_resolved(self, payer_harness: dict[str, Any]) -> None:
        vendor_request_id = _file_enrollment(payer_harness)
        payer_harness["clearinghouse"].listing = [
            enrollment_fixture(vendor_id=vendor_request_id, status="PROVIDER_ACTION_REQUIRED")
        ]
        payer = payer_harness["payer"]

        response = payer_harness["client"].get(
            f"/api/payers/{payer.id}/enrollments/835/tasks/{_DOCUMENT_TASK_ID}/links/0"
        )

        assert response.status_code == 404


class TestCompleteEnrollmentTask:
    def test_uploads_a_pdf_and_completes_the_task(self, payer_harness: dict[str, Any]) -> None:
        vendor_request_id = _file_enrollment(payer_harness)
        payer_harness["clearinghouse"].listing = [
            enrollment_fixture(vendor_id=vendor_request_id, status="PROVIDER_ACTION_REQUIRED")
        ]
        payer = payer_harness["payer"]

        response = payer_harness["client"].post(
            f"/api/payers/{payer.id}/enrollments/835/tasks/{_DOCUMENT_TASK_ID}/complete",
            files={"signed_eft_form": ("form.pdf", b"%PDF-1.4 fake", "application/pdf")},
        )

        assert response.status_code == 200, response.text
        completed = next(t for t in response.json()["tasks"] if t["id"] == _DOCUMENT_TASK_ID)
        assert completed["is_complete"] is True

    def test_a_task_with_no_fields_needs_no_upload(self, payer_harness: dict[str, Any]) -> None:
        vendor_request_id = _file_enrollment(payer_harness)
        payer_harness["clearinghouse"].listing = [
            enrollment_fixture(vendor_id=vendor_request_id, status="PROVIDER_ACTION_REQUIRED")
        ]
        payer = payer_harness["payer"]

        response = payer_harness["client"].post(
            f"/api/payers/{payer.id}/enrollments/835/tasks/{_EMPTY_TASK_ID}/complete"
        )

        assert response.status_code == 200, response.text

    def test_a_non_pdf_file_is_rejected(self, payer_harness: dict[str, Any]) -> None:
        vendor_request_id = _file_enrollment(payer_harness)
        payer_harness["clearinghouse"].listing = [
            enrollment_fixture(vendor_id=vendor_request_id, status="PROVIDER_ACTION_REQUIRED")
        ]
        payer = payer_harness["payer"]

        response = payer_harness["client"].post(
            f"/api/payers/{payer.id}/enrollments/835/tasks/{_DOCUMENT_TASK_ID}/complete",
            files={"signed_eft_form": ("form.txt", b"not a pdf", "text/plain")},
        )

        assert response.status_code == 422

    def test_a_missing_field_is_rejected(self, payer_harness: dict[str, Any]) -> None:
        vendor_request_id = _file_enrollment(payer_harness)
        payer_harness["clearinghouse"].listing = [
            enrollment_fixture(vendor_id=vendor_request_id, status="PROVIDER_ACTION_REQUIRED")
        ]
        payer = payer_harness["payer"]

        response = payer_harness["client"].post(
            f"/api/payers/{payer.id}/enrollments/835/tasks/{_DOCUMENT_TASK_ID}/complete"
        )

        assert response.status_code == 422


# --- the coverage-save trigger, over in-memory repositories ----------------------


class _FakePatients:
    def __init__(self) -> None:
        now = datetime.now(UTC)
        self.patient = Patient(
            id=_PATIENT_ID, first_name="A", last_name="B", created_at=now, updated_at=now
        )

    def get(self, patient_id: str, user_id: str) -> Patient | None:
        return self.patient if patient_id == _PATIENT_ID and user_id == _USER_ID else None


@pytest.fixture
def coverage_harness() -> dict[str, Any]:
    payers = InMemoryPayerRepository()
    coverage = InMemoryPatientCoverageRepository()
    triggered: list[tuple[str, str]] = []

    app = FastAPI()
    app.include_router(coverage_routes.router)
    app.dependency_overrides[require_baa_acceptance] = _user
    app.dependency_overrides[get_tenant_context] = _tenant
    app.dependency_overrides[get_payer_repository] = lambda: payers
    app.dependency_overrides[get_patient_coverage_repository] = lambda: coverage
    app.dependency_overrides[get_patient_repository] = _FakePatients
    app.dependency_overrides[get_audit_service] = lambda: AuditService(InMemoryAuditRepository())
    app.dependency_overrides[coverage_routes.get_enrollment_trigger] = lambda: (
        lambda payer_row_id, user_id: triggered.append((payer_row_id, user_id))
    )
    # The eligibility check that rides the same save is its own flow
    # (test_coverage_routes.py); off here.
    app.dependency_overrides[get_eligibility_auto_check] = lambda: EligibilityAutoCheck(
        enabled=False, schedule=lambda _c, _u, _t: None
    )
    app.dependency_overrides[coverage_routes.get_clearinghouse_client] = lambda: None
    app.dependency_overrides[coverage_routes.get_billing_identity] = lambda: None
    return {
        "client": TestClient(app, raise_server_exceptions=False),
        "payers": payers,
        "triggered": triggered,
    }


class TestCoverageTrigger:
    def test_putting_a_plan_on_file_enrolls_with_its_payer(
        self, coverage_harness: dict[str, Any]
    ) -> None:
        response = coverage_harness["client"].post(
            f"/api/patients/{_PATIENT_ID}/coverage",
            json={"new_payer": {"name": "Aetna", "payer_id": "60054"}, "member_id": "W1"},
        )

        assert response.status_code == 201, response.text
        payer_row_id = response.json()["payer"]["id"]
        assert coverage_harness["triggered"] == [(payer_row_id, _USER_ID)]

    def test_switching_payer_enrolls_with_the_new_one(
        self, coverage_harness: dict[str, Any]
    ) -> None:
        client = coverage_harness["client"]
        other = coverage_harness["payers"].create(new_payer(name="Cigna", payer_id="62308"))
        client.post(
            f"/api/patients/{_PATIENT_ID}/coverage",
            json={"new_payer": {"name": "Aetna", "payer_id": "60054"}, "member_id": "W1"},
        )
        coverage_harness["triggered"].clear()

        client.patch(f"/api/patients/{_PATIENT_ID}/coverage", json={"member_id": "W2"})
        assert coverage_harness["triggered"] == []

        client.patch(f"/api/patients/{_PATIENT_ID}/coverage", json={"payer_id": other.id})
        assert coverage_harness["triggered"] == [(other.id, _USER_ID)]

    def test_the_real_trigger_never_fails_the_save(
        self, coverage_harness: dict[str, Any], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A clearinghouse that blows up leaves the plan on file."""
        app = coverage_harness["client"].app
        del app.dependency_overrides[coverage_routes.get_enrollment_trigger]  # type: ignore[attr-defined]  # Starlette typing
        calls: list[str] = []

        def enroll_if_new(
            session: Any, practice_id: str | None, *, payer_row_id: str, user_id: str
        ) -> None:
            calls.append(payer_row_id)

        monkeypatch.setattr(coverage_routes, "enroll_if_new", enroll_if_new)
        monkeypatch.setattr(coverage_routes, "get_db_session", lambda: None)

        response = coverage_harness["client"].post(
            f"/api/patients/{_PATIENT_ID}/coverage",
            json={"new_payer": {"name": "Aetna", "payer_id": "60054"}, "member_id": "W1"},
        )

        assert response.status_code == 201, response.text
        assert calls == [response.json()["payer"]["id"]]
