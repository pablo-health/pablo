# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Unit tests for the biller export routes (``app.routes.claims_export``).

What these pin down:

* ``GET /api/claims/export.csv`` is a CSV download with the fixed header
  and one row per line of every validated-or-later claim dated in the
  range; a draft in the range is left out;
* a claim that would leave with a blocking finding turns the whole export
  into a 422 that names it and its findings;
* ``GET /api/claims/{id}/cms1500.pdf`` is a PDF download; 409 on a draft,
  404 for an unknown claim or one whose client the caller cannot see;
* each export writes one audit row carrying claim ids and control numbers
  only — no member id, no date of birth, no diagnosis — and nothing off
  the card reaches a log line either.

Hermetic: in-memory repositories throughout, claims seeded directly.
"""

from __future__ import annotations

import csv
import json
import logging
from datetime import UTC, date, datetime
from io import StringIO
from typing import Any

import pytest
from app.api_errors import register_exception_handlers
from app.auth.service import require_baa_acceptance
from app.claims.export import CSV_COLUMNS
from app.models import User
from app.models.patient import Patient
from app.repositories import get_claim_repository, get_patient_repository
from app.repositories.audit import InMemoryAuditRepository
from app.repositories.claims import InMemoryClaimRepository
from app.repositories.patient import InMemoryPatientRepository
from app.routes import claims as claims_routes
from app.routes import claims_export
from app.services import AuditService, get_audit_service
from fastapi import FastAPI
from fastapi.testclient import TestClient

from tests.claims_fixtures import PATIENT_ID, USER_ID, claim, line

_NOW = datetime(2026, 9, 2, 15, 0, tzinfo=UTC)
_MEMBER_ID = "123456789"
_DOB = "2000-01-01"
_DX = "F41.1"
_RANGE = {"from": "2026-09-01", "to": "2026-09-30"}


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
    audit_repo = InMemoryAuditRepository()

    app = FastAPI()
    register_exception_handlers(app)
    # Mounted in the order the application mounts them: the export router
    # first, so ``export.csv`` is not read as a claim id.
    app.include_router(claims_export.router)
    app.include_router(claims_routes.router)
    app.dependency_overrides[require_baa_acceptance] = _user
    app.dependency_overrides[get_patient_repository] = lambda: patients
    app.dependency_overrides[get_claim_repository] = lambda: claims
    app.dependency_overrides[get_audit_service] = lambda: AuditService(audit_repo)
    client = TestClient(app, raise_server_exceptions=False)
    return {"app": app, "client": client, "claims": claims, "audit": audit_repo}


def _seed(harness: dict[str, Any], **overrides: Any) -> str:
    fields: dict[str, Any] = {"state": "validated"}
    fields.update(overrides)
    seeded = claim(**fields)
    harness["claims"].create(seeded)
    return seeded.id


def _rows(text: str) -> list[dict[str, str]]:
    return list(csv.DictReader(StringIO(text)))


def _audit_rows(harness: dict[str, Any]) -> list[Any]:
    return [row for row in harness["audit"]._entries if row.user_id == USER_ID]


def _assert_nothing_off_the_card(rows: list[Any]) -> None:
    text = json.dumps([row.changes for row in rows])
    assert _MEMBER_ID not in text
    assert _DOB not in text
    assert _DX not in text


class TestCsv:
    def test_downloads_one_row_per_line_of_each_claim_in_range(
        self, harness: dict[str, Any]
    ) -> None:
        _seed(harness)
        _seed(
            harness,
            id="second",
            control_number="SECOND1",
            total_charge_cents=21000,
            lines=[
                line(id="l1", claim_id="second"),
                line(
                    id="l2",
                    claim_id="second",
                    line_number=2,
                    line_control_number="886598912",
                    cpt="90833",
                    charge_cents=6000,
                ),
            ],
        )
        resp = harness["client"].get("/api/claims/export.csv", params=_RANGE)
        assert resp.status_code == 200, resp.text
        assert resp.headers["content-type"].startswith("text/csv")
        assert resp.headers["content-disposition"] == (
            'attachment; filename="claims-2026-09-01-2026-09-30.csv"'
        )
        assert next(csv.reader(StringIO(resp.text))) == list(CSV_COLUMNS)
        assert [(r["control_number"], r["cpt"]) for r in _rows(resp.text)] == [
            ("88659891", "90837"),
            ("SECOND1", "90837"),
            ("SECOND1", "90833"),
        ]

    def test_a_draft_in_the_range_is_excluded(self, harness: dict[str, Any]) -> None:
        _seed(harness)
        _seed(harness, id="draft", control_number="DRAFT1", state="draft")
        resp = harness["client"].get("/api/claims/export.csv", params=_RANGE)
        assert resp.status_code == 200
        assert [r["control_number"] for r in _rows(resp.text)] == ["88659891"]

    def test_a_claim_dated_outside_the_range_is_excluded(self, harness: dict[str, Any]) -> None:
        _seed(harness, lines=[line(service_date=date(2026, 8, 31))])
        resp = harness["client"].get("/api/claims/export.csv", params=_RANGE)
        assert resp.status_code == 200
        assert _rows(resp.text) == []

    def test_a_range_that_ends_before_it_starts_is_422(self, harness: dict[str, Any]) -> None:
        resp = harness["client"].get(
            "/api/claims/export.csv", params={"from": "2026-09-30", "to": "2026-09-01"}
        )
        assert resp.status_code == 422

    def test_a_blocking_finding_refuses_and_lists(self, harness: dict[str, Any]) -> None:
        _seed(harness)
        _seed(harness, id="bad", control_number="BAD1", diagnosis_codes=["F41"])
        resp = harness["client"].get("/api/claims/export.csv", params=_RANGE)
        assert resp.status_code == 422
        error = resp.json()["error"]
        assert error["code"] == "CLAIM_EXPORT_BLOCKED"
        (blocked,) = error["details"]["claims"]
        assert blocked["claim_id"] == "bad"
        assert blocked["control_number"] == "BAD1"
        assert "dx_not_specific" in {f["code"] for f in blocked["findings"]}
        assert _audit_rows(harness) == []

    def test_is_audited_with_claim_ids_and_control_numbers_only(
        self, harness: dict[str, Any]
    ) -> None:
        _seed(harness)
        _seed(harness, id="second", control_number="SECOND1")
        resp = harness["client"].get("/api/claims/export.csv", params=_RANGE)
        assert resp.status_code == 200
        (row,) = _audit_rows(harness)
        assert row.action == "claims_exported"
        assert row.resource_type == "claim_export"
        assert row.resource_id == "2026-09-01..2026-09-30"
        assert row.changes == {
            "format": "csv",
            "from": "2026-09-01",
            "to": "2026-09-30",
            "count": 2,
            "claim_ids": ["aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa", "second"],
            "control_numbers": ["88659891", "SECOND1"],
        }
        _assert_nothing_off_the_card([row])

    def test_an_empty_range_is_a_header_only_file(self, harness: dict[str, Any]) -> None:
        resp = harness["client"].get("/api/claims/export.csv", params=_RANGE)
        assert resp.status_code == 200
        assert resp.text.strip() == ",".join(CSV_COLUMNS)
        (row,) = _audit_rows(harness)
        assert row.changes["count"] == 0


class TestPdf:
    def test_downloads_the_cms1500_pdf(self, harness: dict[str, Any]) -> None:
        claim_id = _seed(harness)
        resp = harness["client"].get(f"/api/claims/{claim_id}/cms1500.pdf")
        assert resp.status_code == 200, resp.text
        assert resp.headers["content-type"] == "application/pdf"
        assert resp.headers["content-disposition"] == 'attachment; filename="claim-88659891.pdf"'
        assert resp.content.startswith(b"%PDF")

    def test_a_draft_is_409(self, harness: dict[str, Any]) -> None:
        claim_id = _seed(harness, state="draft")
        resp = harness["client"].get(f"/api/claims/{claim_id}/cms1500.pdf")
        assert resp.status_code == 409
        assert _audit_rows(harness) == []

    def test_a_blocking_finding_refuses_and_lists(self, harness: dict[str, Any]) -> None:
        claim_id = _seed(harness, diagnosis_codes=["F41"])
        resp = harness["client"].get(f"/api/claims/{claim_id}/cms1500.pdf")
        assert resp.status_code == 422
        error = resp.json()["error"]
        assert error["code"] == "CLAIM_EXPORT_BLOCKED"
        assert [c["control_number"] for c in error["details"]["claims"]] == ["88659891"]

    def test_unknown_claim_is_404(self, harness: dict[str, Any]) -> None:
        assert harness["client"].get("/api/claims/nope/cms1500.pdf").status_code == 404

    def test_another_clinicians_claim_is_404_not_403(self, harness: dict[str, Any]) -> None:
        claim_id = _seed(harness)
        harness["app"].dependency_overrides[require_baa_acceptance] = lambda: _user("other")
        resp = harness["client"].get(f"/api/claims/{claim_id}/cms1500.pdf")
        assert resp.status_code == 404

    def test_is_audited_against_the_claim_and_its_client(self, harness: dict[str, Any]) -> None:
        claim_id = _seed(harness)
        resp = harness["client"].get(f"/api/claims/{claim_id}/cms1500.pdf")
        assert resp.status_code == 200
        (row,) = _audit_rows(harness)
        assert row.action == "claim_exported"
        assert row.resource_type == "claim"
        assert row.resource_id == claim_id
        assert row.patient_id == PATIENT_ID
        assert row.changes == {
            "format": "cms1500_pdf",
            "claim_id": claim_id,
            "control_number": "88659891",
            "state": "validated",
            "payer_id": "33333333-3333-4333-8333-333333333333",
        }
        _assert_nothing_off_the_card([row])


class TestLogs:
    def test_nothing_off_the_card_reaches_a_log_line(
        self, harness: dict[str, Any], caplog: pytest.LogCaptureFixture
    ) -> None:
        claim_id = _seed(harness)
        _seed(harness, id="bad", control_number="BAD1", diagnosis_codes=["F41"])
        with caplog.at_level(logging.DEBUG):
            harness["client"].get("/api/claims/export.csv", params=_RANGE)
            harness["client"].get(f"/api/claims/{claim_id}/cms1500.pdf")
        assert _MEMBER_ID not in caplog.text
        assert _DOB not in caplog.text
        assert _DX not in caplog.text
        assert "F41" not in caplog.text


class TestRouteOrder:
    def test_export_csv_is_not_read_as_a_claim_id(self, harness: dict[str, Any]) -> None:
        """With both routers mounted, ``export.csv`` reaches the export route, not 404."""
        resp = harness["client"].get("/api/claims/export.csv", params=_RANGE)
        assert resp.status_code == 200
