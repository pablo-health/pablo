# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Unit tests for coverage on file (``app.routes.coverage``).

What these pin down:

* a plan round-trips through the chart card's API — create, read, update,
  deactivate — and a deactivated plan is gone from the read, not deleted;
* one active coverage per client: a second create is 409;
* the payer picker's free-text fallback adds a ``payers`` row on the way
  through, with deadlines defaulted for the payer id (Medicare gets a year);
* an unknown or ungranted client is 404, never 403;
* the audit rows for every coverage access carry the coverage and payer row
  ids and nothing off the card — no member id, no subscriber.

Hermetic: the repositories are the in-memory implementations the public
booking tests share, and the audit service writes to an in-memory repository
so the rows can be read back and inspected.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from app.auth.service import (
    TenantContext,
    get_tenant_context,
    require_active_subscription,
    require_baa_acceptance,
)
from app.claims.clearinghouse import ClearinghouseUnavailableError, ClearinghouseValidationError
from app.claims.eligibility import BillingIdentity, EligibilityAutoCheck, get_eligibility_auto_check
from app.models import User
from app.models.claims_transport import EligibilityRequest, EligibilityResponse
from app.models.patient import Patient
from app.repositories import (
    get_patient_coverage_repository,
    get_patient_repository,
    get_payer_repository,
)
from app.repositories.audit import InMemoryAuditRepository
from app.repositories.coverage import InMemoryPatientCoverageRepository, InMemoryPayerRepository
from app.routes import coverage as coverage_routes
from app.services import AuditService, get_audit_service
from fastapi import FastAPI
from fastapi.testclient import TestClient

_FIXTURES = Path(__file__).parent / "fixtures" / "clearinghouse"

_USER_ID = "user-1"
_PATIENT_ID = "11111111-1111-4111-8111-111111111111"
_OTHER_PATIENT_ID = "22222222-2222-4222-8222-222222222222"
_MEMBER_ID = "W123456789"
_SUBSCRIBER_FIRST = "Parent"
_SUBSCRIBER_LAST = "Person"


class _FakePatients:
    """One visible client; everyone else absent."""

    def __init__(self, *, visible: bool = True) -> None:
        self.visible = visible
        self.patient = Patient(
            id=_PATIENT_ID,
            first_name="A",
            last_name="B",
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )

    def get(self, patient_id: str, user_id: str) -> Patient | None:
        if not self.visible or patient_id != _PATIENT_ID or user_id != _USER_ID:
            return None
        return self.patient


def _user() -> User:
    return User(
        id=_USER_ID,
        email="therapist@example.com",
        name="Test Therapist",
        created_at=datetime.now(UTC),
        baa_accepted_at=datetime.now(UTC),
        baa_version="2024-01-01",
    )


class _FakeClearinghouse:
    """Answers every 270 with one recorded 271, or raises what it was told to."""

    def __init__(self, fixture: str = "eligibility_271_active.json") -> None:
        self.fixture = fixture
        self.raises: Exception | None = None
        self.inquiries: list[EligibilityRequest] = []

    def check_eligibility(self, req: EligibilityRequest) -> EligibilityResponse:
        self.inquiries.append(req)
        if self.raises is not None:
            raise self.raises
        body = json.loads((_FIXTURES / self.fixture).read_text())
        return EligibilityResponse.model_validate(body)


@pytest.fixture
def harness() -> dict[str, Any]:
    payers = InMemoryPayerRepository()
    coverage = InMemoryPatientCoverageRepository()
    patients = _FakePatients()
    audit_repo = InMemoryAuditRepository()
    clearinghouse = _FakeClearinghouse()
    queued: list[tuple[str, str, str]] = []
    auto_check = EligibilityAutoCheck(
        enabled=True, schedule=lambda c, u, t: queued.append((c, u, t))
    )

    app = FastAPI()
    app.include_router(coverage_routes.payers_router)
    app.include_router(coverage_routes.router)
    app.dependency_overrides[require_baa_acceptance] = _user
    app.dependency_overrides[require_active_subscription] = _user
    app.dependency_overrides[get_tenant_context] = lambda: TenantContext(
        user_id=_USER_ID, practice_id="practice-1", practice_schema="practice_x"
    )
    app.dependency_overrides[get_payer_repository] = lambda: payers
    app.dependency_overrides[get_patient_coverage_repository] = lambda: coverage
    app.dependency_overrides[get_patient_repository] = lambda: patients
    app.dependency_overrides[get_audit_service] = lambda: AuditService(audit_repo)
    app.dependency_overrides[get_eligibility_auto_check] = lambda: auto_check
    app.dependency_overrides[coverage_routes.get_clearinghouse_client] = lambda: clearinghouse
    app.dependency_overrides[coverage_routes.get_billing_identity] = lambda: BillingIdentity(
        npi="1999999984", organization_name="Test Practice"
    )
    client = TestClient(app, raise_server_exceptions=False)
    return {
        "client": client,
        "payers": payers,
        "coverage": coverage,
        "patients": patients,
        "audit": audit_repo,
        "clearinghouse": clearinghouse,
        "queued": queued,
        "auto_check": auto_check,
        "app": app,
    }


def _audit_rows(harness: dict[str, Any]) -> list[Any]:
    return harness["audit"].list_for_user(_USER_ID)


def _coverage_payload(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "new_payer": {"name": "Aetna", "payer_id": "60054"},
        "member_id": _MEMBER_ID,
        "group_number": "GRP-77",
        "plan_name": "Choice POS II",
    }
    payload.update(overrides)
    return payload


# ---------------------------------------------------------------------------
# Payers
# ---------------------------------------------------------------------------


class TestPayers:
    def test_list_starts_empty(self, harness: dict[str, Any]) -> None:
        resp = harness["client"].get("/api/payers")
        assert resp.status_code == 200
        assert resp.json() == {"data": [], "total": 0}

    def test_create_defaults_the_deadlines(self, harness: dict[str, Any]) -> None:
        resp = harness["client"].post("/api/payers", json={"name": "Aetna", "payer_id": "60054"})
        assert resp.status_code == 201
        body = resp.json()
        assert body["timely_filing_days"] == 90
        assert body["corrected_claim_days"] == 90
        assert body["appeal_days"] == 180
        assert body["enrollment_status"] == "none"
        assert body["clearinghouse_payer_id"] is None

    def test_medicare_payer_id_files_within_a_year(self, harness: dict[str, Any]) -> None:
        resp = harness["client"].post(
            "/api/payers", json={"name": "Medicare Part B", "payer_id": "MEDICARE-OH"}
        )
        assert resp.status_code == 201
        assert resp.json()["timely_filing_days"] == 365

    def test_update_edits_deadlines_and_keeps_the_rest(self, harness: dict[str, Any]) -> None:
        created = (
            harness["client"]
            .post("/api/payers", json={"name": "Aetna", "payer_id": "60054"})
            .json()
        )

        resp = harness["client"].patch(
            f"/api/payers/{created['id']}", json={"timely_filing_days": 180, "appeal_days": 60}
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["timely_filing_days"] == 180
        assert body["corrected_claim_days"] == 90
        assert body["appeal_days"] == 60
        assert body["name"] == "Aetna"

    def test_deadlines_must_be_positive(self, harness: dict[str, Any]) -> None:
        resp = harness["client"].post(
            "/api/payers", json={"name": "Aetna", "payer_id": "60054", "appeal_days": 0}
        )
        assert resp.status_code == 422

    def test_carveout_must_name_a_payer_on_the_list(self, harness: dict[str, Any]) -> None:
        resp = harness["client"].post(
            "/api/payers",
            json={
                "name": "Behavioral Carve-out",
                "payer_id": "BH1",
                "is_carveout": True,
                "carveout_of": "not-a-payer",
            },
        )
        assert resp.status_code == 404

    def test_a_payer_cannot_carve_out_of_itself(self, harness: dict[str, Any]) -> None:
        created = (
            harness["client"]
            .post("/api/payers", json={"name": "Aetna", "payer_id": "60054"})
            .json()
        )
        resp = harness["client"].patch(
            f"/api/payers/{created['id']}", json={"carveout_of": created["id"]}
        )
        assert resp.status_code == 422

    def test_unknown_payer_is_404(self, harness: dict[str, Any]) -> None:
        resp = harness["client"].patch("/api/payers/nope", json={"name": "x"})
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Coverage round trip
# ---------------------------------------------------------------------------


class TestCoverageRoundTrip:
    def test_no_coverage_is_404(self, harness: dict[str, Any]) -> None:
        resp = harness["client"].get(f"/api/patients/{_PATIENT_ID}/coverage")
        assert resp.status_code == 404

    def test_create_with_a_typed_payer_adds_the_payer_row(self, harness: dict[str, Any]) -> None:
        resp = harness["client"].post(
            f"/api/patients/{_PATIENT_ID}/coverage", json=_coverage_payload()
        )
        assert resp.status_code == 201
        body = resp.json()
        assert body["patient_id"] == _PATIENT_ID
        assert body["member_id"] == _MEMBER_ID
        assert body["group_number"] == "GRP-77"
        assert body["plan_name"] == "Choice POS II"
        assert body["subscriber_relationship"] == "self"
        assert body["active"] is True
        assert body["verified_at"] is None
        assert body["payer"]["name"] == "Aetna"
        assert body["payer"]["payer_id"] == "60054"
        assert body["payer"]["timely_filing_days"] == 90
        assert "last_271" not in body

        (payer,) = harness["payers"].list()
        assert payer.id == body["payer"]["id"]

    def test_create_with_a_listed_payer(self, harness: dict[str, Any]) -> None:
        payer = (
            harness["client"]
            .post("/api/payers", json={"name": "Cigna", "payer_id": "62308"})
            .json()
        )

        resp = harness["client"].post(
            f"/api/patients/{_PATIENT_ID}/coverage",
            json=_coverage_payload(new_payer=None, payer_id=payer["id"]),
        )
        assert resp.status_code == 201
        assert resp.json()["payer"]["id"] == payer["id"]
        assert len(harness["payers"].list()) == 1

    def test_create_needs_exactly_one_way_of_naming_the_payer(
        self, harness: dict[str, Any]
    ) -> None:
        neither = harness["client"].post(
            f"/api/patients/{_PATIENT_ID}/coverage", json=_coverage_payload(new_payer=None)
        )
        assert neither.status_code == 422

        both = harness["client"].post(
            f"/api/patients/{_PATIENT_ID}/coverage", json=_coverage_payload(payer_id="p1")
        )
        assert both.status_code == 422

    def test_create_with_an_unlisted_payer_id_is_404(self, harness: dict[str, Any]) -> None:
        resp = harness["client"].post(
            f"/api/patients/{_PATIENT_ID}/coverage",
            json=_coverage_payload(new_payer=None, payer_id="nope"),
        )
        assert resp.status_code == 404

    def test_read_returns_what_was_created(self, harness: dict[str, Any]) -> None:
        created = (
            harness["client"]
            .post(
                f"/api/patients/{_PATIENT_ID}/coverage",
                json=_coverage_payload(
                    subscriber_relationship="child",
                    subscriber_first_name=_SUBSCRIBER_FIRST,
                    subscriber_last_name=_SUBSCRIBER_LAST,
                    subscriber_date_of_birth="1980-02-03",
                    subscriber_sex="F",
                ),
            )
            .json()
        )

        resp = harness["client"].get(f"/api/patients/{_PATIENT_ID}/coverage")
        assert resp.status_code == 200
        body = resp.json()
        assert body["id"] == created["id"]
        assert body["subscriber_relationship"] == "child"
        assert body["subscriber_first_name"] == _SUBSCRIBER_FIRST
        assert body["subscriber_date_of_birth"] == "1980-02-03"
        assert body["subscriber_sex"] == "F"

    def test_second_active_coverage_is_409(self, harness: dict[str, Any]) -> None:
        first = harness["client"].post(
            f"/api/patients/{_PATIENT_ID}/coverage", json=_coverage_payload()
        )
        assert first.status_code == 201

        second = harness["client"].post(
            f"/api/patients/{_PATIENT_ID}/coverage", json=_coverage_payload(member_id="OTHER")
        )
        assert second.status_code == 409

    def test_update_is_partial(self, harness: dict[str, Any]) -> None:
        harness["client"].post(f"/api/patients/{_PATIENT_ID}/coverage", json=_coverage_payload())

        resp = harness["client"].patch(
            f"/api/patients/{_PATIENT_ID}/coverage", json={"member_id": "NEW-ID"}
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["member_id"] == "NEW-ID"
        assert body["group_number"] == "GRP-77"
        assert body["payer"]["name"] == "Aetna"

    def test_update_can_switch_to_a_listed_payer(self, harness: dict[str, Any]) -> None:
        harness["client"].post(f"/api/patients/{_PATIENT_ID}/coverage", json=_coverage_payload())
        cigna = (
            harness["client"]
            .post("/api/payers", json={"name": "Cigna", "payer_id": "62308"})
            .json()
        )

        resp = harness["client"].patch(
            f"/api/patients/{_PATIENT_ID}/coverage", json={"payer_id": cigna["id"]}
        )
        assert resp.status_code == 200
        assert resp.json()["payer"]["name"] == "Cigna"

    def test_update_with_nothing_on_file_is_404(self, harness: dict[str, Any]) -> None:
        resp = harness["client"].patch(
            f"/api/patients/{_PATIENT_ID}/coverage", json={"member_id": "x"}
        )
        assert resp.status_code == 404

    def test_deactivate_takes_it_off_file_but_keeps_the_row(self, harness: dict[str, Any]) -> None:
        created = (
            harness["client"]
            .post(f"/api/patients/{_PATIENT_ID}/coverage", json=_coverage_payload())
            .json()
        )

        resp = harness["client"].delete(f"/api/patients/{_PATIENT_ID}/coverage")
        assert resp.status_code == 204
        assert harness["client"].get(f"/api/patients/{_PATIENT_ID}/coverage").status_code == 404

        kept = harness["coverage"]._rows[created["id"]]
        assert kept.active is False
        assert kept.member_id == _MEMBER_ID

        # And a fresh plan can go on file afterwards.
        again = harness["client"].post(
            f"/api/patients/{_PATIENT_ID}/coverage", json=_coverage_payload(member_id="NEXT")
        )
        assert again.status_code == 201

    def test_deactivate_with_nothing_on_file_is_404(self, harness: dict[str, Any]) -> None:
        resp = harness["client"].delete(f"/api/patients/{_PATIENT_ID}/coverage")
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Access
# ---------------------------------------------------------------------------


class TestAccess:
    @pytest.mark.parametrize("method", ["get", "post", "patch", "delete"])
    def test_unknown_client_is_404_not_403(self, harness: dict[str, Any], method: str) -> None:
        url = f"/api/patients/{_OTHER_PATIENT_ID}/coverage"
        payload = _coverage_payload() if method == "post" else {"member_id": "x"}
        resp = (
            harness["client"].request(method, url, json=payload)
            if method != "get"
            else harness["client"].get(url)
        )
        assert resp.status_code == 404

    def test_ungranted_client_reads_as_absent(self, harness: dict[str, Any]) -> None:
        harness["patients"].visible = False
        resp = harness["client"].get(f"/api/patients/{_PATIENT_ID}/coverage")
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Audit
# ---------------------------------------------------------------------------


class TestAudit:
    def test_every_access_is_audited_with_ids_only(self, harness: dict[str, Any]) -> None:
        client = harness["client"]
        created = client.post(
            f"/api/patients/{_PATIENT_ID}/coverage",
            json=_coverage_payload(
                subscriber_relationship="spouse",
                subscriber_first_name=_SUBSCRIBER_FIRST,
                subscriber_last_name=_SUBSCRIBER_LAST,
            ),
        ).json()
        client.get(f"/api/patients/{_PATIENT_ID}/coverage")
        client.patch(f"/api/patients/{_PATIENT_ID}/coverage", json={"member_id": "NEW-ID"})
        client.delete(f"/api/patients/{_PATIENT_ID}/coverage")

        rows = _audit_rows(harness)
        actions = sorted(str(row.action) for row in rows)
        assert actions == [
            "patient_coverage_created",
            "patient_coverage_deactivated",
            "patient_coverage_updated",
            "patient_coverage_viewed",
        ]
        for row in rows:
            assert row.resource_id == _PATIENT_ID
            assert row.changes == {
                "coverage_id": created["id"],
                "payer_id": created["payer"]["id"],
            }
            serialized = str(row.changes)
            assert _MEMBER_ID not in serialized
            assert "NEW-ID" not in serialized
            assert _SUBSCRIBER_FIRST not in serialized
            assert _SUBSCRIBER_LAST not in serialized

    def test_a_404_leaves_no_audit_row(self, harness: dict[str, Any]) -> None:
        harness["client"].get(f"/api/patients/{_PATIENT_ID}/coverage")
        assert _audit_rows(harness) == []


# ---------------------------------------------------------------------------
# Eligibility
# ---------------------------------------------------------------------------


_VERIFY = f"/api/patients/{_PATIENT_ID}/coverage/verify"


def _put_on_file(harness: dict[str, Any], **overrides: Any) -> dict[str, Any]:
    resp = harness["client"].post(
        f"/api/patients/{_PATIENT_ID}/coverage", json=_coverage_payload(**overrides)
    )
    assert resp.status_code == 201
    return resp.json()


class TestAutoCheck:
    def test_saving_coverage_queues_a_check_when_on(self, harness: dict[str, Any]) -> None:
        created = _put_on_file(harness)
        harness["client"].patch(f"/api/patients/{_PATIENT_ID}/coverage", json={"member_id": "X"})

        assert harness["queued"] == [
            (created["id"], _USER_ID, "save"),
            (created["id"], _USER_ID, "save"),
        ]

    def test_saving_coverage_queues_nothing_when_off(self, harness: dict[str, Any]) -> None:
        harness["auto_check"].enabled = False

        _put_on_file(harness)
        harness["client"].patch(f"/api/patients/{_PATIENT_ID}/coverage", json={"member_id": "X"})

        assert harness["queued"] == []

    def test_the_manual_button_runs_regardless(self, harness: dict[str, Any]) -> None:
        harness["auto_check"].enabled = False
        _put_on_file(harness)

        resp = harness["client"].post(_VERIFY)

        assert resp.status_code == 200
        assert len(harness["clearinghouse"].inquiries) == 1
        assert harness["queued"] == []

    def test_editing_the_plan_clears_the_old_answer(self, harness: dict[str, Any]) -> None:
        _put_on_file(harness)
        harness["client"].post(_VERIFY)
        assert harness["client"].get(f"/api/patients/{_PATIENT_ID}/coverage").json()["eligibility"]

        resp = harness["client"].patch(
            f"/api/patients/{_PATIENT_ID}/coverage", json={"member_id": "CHANGED"}
        )

        assert resp.json()["eligibility"] is None
        assert resp.json()["verified_at"] is None


class TestVerify:
    def test_asks_about_mental_health_and_returns_the_summary(
        self, harness: dict[str, Any]
    ) -> None:
        _put_on_file(harness)

        resp = harness["client"].post(_VERIFY)

        assert resp.status_code == 200
        body = resp.json()
        assert body["verified_at"] is not None
        assert body["eligibility"]["status"] == "active"
        assert body["eligibility"]["payer_name"] == "UNITEDHEALTHCARE"
        assert body["eligibility"]["carveout_administrator"] is None
        assert "last_271" not in body

        (inquiry,) = harness["clearinghouse"].inquiries
        assert inquiry.encounter is not None
        assert inquiry.encounter.serviceTypeCodes == ["MH"]
        assert inquiry.subscriber.memberId == _MEMBER_ID
        assert inquiry.tradingPartnerServiceId == "60054"
        assert inquiry.provider.npi == "1999999984"

    def test_the_raw_271_is_kept_on_the_row(self, harness: dict[str, Any]) -> None:
        created = _put_on_file(harness)

        harness["client"].post(_VERIFY)

        stored = harness["coverage"]._rows[created["id"]]
        assert stored.last_271 is not None
        assert stored.last_271["payer"]["name"] == "UNITEDHEALTHCARE"
        assert stored.verified_at is not None
        # And the next read renders it without asking the payer again.
        read = harness["client"].get(f"/api/patients/{_PATIENT_ID}/coverage").json()
        assert read["eligibility"]["status"] == "active"
        assert len(harness["clearinghouse"].inquiries) == 1

    def test_a_carveout_names_the_administrator(self, harness: dict[str, Any]) -> None:
        harness["clearinghouse"].fixture = "eligibility_271_carveout_behavioral.json"
        _put_on_file(harness)

        body = harness["client"].post(_VERIFY).json()

        assert body["eligibility"]["carveout_administrator"] == {
            "name": "EXAMPLE BEHAVIORAL HEALTH",
            "payer_id": "EXBH1",
        }

    def test_an_inactive_plan(self, harness: dict[str, Any]) -> None:
        harness["clearinghouse"].fixture = "eligibility_271_inactive.json"
        _put_on_file(harness)

        body = harness["client"].post(_VERIFY).json()

        assert body["eligibility"]["status"] == "inactive"

    def test_a_payer_refusal_is_stored_as_the_answer(self, harness: dict[str, Any]) -> None:
        harness["clearinghouse"].fixture = "eligibility_271_aaa_invalid_member_id.json"
        _put_on_file(harness)

        resp = harness["client"].post(_VERIFY)

        assert resp.status_code == 200
        summary = resp.json()["eligibility"]
        assert summary["status"] == "error"
        assert [e["code"] for e in summary["aaa_errors"]] == ["72"]
        assert summary["aaa_errors"][0]["resolution"]

    def test_a_payer_without_an_electronic_id_is_409(self, harness: dict[str, Any]) -> None:
        _put_on_file(harness, new_payer={"name": "Typed From Card", "payer_id": "UNKNOWN"})

        resp = harness["client"].post(_VERIFY)

        assert resp.status_code == 409
        assert "payer directory" in resp.json()["detail"]
        assert harness["clearinghouse"].inquiries == []

    def test_no_clearinghouse_account_is_409(self, harness: dict[str, Any]) -> None:
        harness["app"].dependency_overrides[coverage_routes.get_clearinghouse_client] = lambda: None
        _put_on_file(harness)

        resp = harness["client"].post(_VERIFY)

        assert resp.status_code == 409
        assert "clearinghouse" in resp.json()["detail"]

    def test_an_unreachable_clearinghouse_is_503(self, harness: dict[str, Any]) -> None:
        harness["clearinghouse"].raises = ClearinghouseUnavailableError("timed out")
        _put_on_file(harness)

        resp = harness["client"].post(_VERIFY)

        assert resp.status_code == 503

    def test_a_refused_inquiry_is_502(self, harness: dict[str, Any]) -> None:
        harness["clearinghouse"].raises = ClearinghouseValidationError("bad request")
        _put_on_file(harness)

        resp = harness["client"].post(_VERIFY)

        assert resp.status_code == 502

    def test_nothing_on_file_is_404(self, harness: dict[str, Any]) -> None:
        assert harness["client"].post(_VERIFY).status_code == 404

    def test_unknown_client_is_404(self, harness: dict[str, Any]) -> None:
        resp = harness["client"].post(f"/api/patients/{_OTHER_PATIENT_ID}/coverage/verify")
        assert resp.status_code == 404


class TestEligibilityAudit:
    def test_every_check_writes_a_row_with_ids_and_outcome_only(
        self, harness: dict[str, Any]
    ) -> None:
        created = _put_on_file(harness)

        harness["client"].post(_VERIFY)
        harness["clearinghouse"].fixture = "eligibility_271_aaa_invalid_member_id.json"
        harness["client"].post(_VERIFY)
        harness["clearinghouse"].raises = ClearinghouseUnavailableError("timed out")
        harness["client"].post(_VERIFY)

        rows = [r for r in _audit_rows(harness) if str(r.action) == "patient_coverage_verified"]
        assert len(rows) == 3
        outcomes = sorted((r.changes or {}).get("status") for r in rows)
        assert outcomes == ["active", "error", "failed"]
        for row in rows:
            assert row.resource_id == _PATIENT_ID
            assert row.changes is not None
            assert row.changes["coverage_id"] == created["id"]
            assert row.changes["trigger"] == "manual"
            assert _MEMBER_ID not in str(row.changes)
            assert "benefitsInformation" not in str(row.changes)
            assert "UHC123456" not in str(row.changes)

    def test_the_271_and_member_id_never_reach_a_log_line(
        self, harness: dict[str, Any], caplog: pytest.LogCaptureFixture
    ) -> None:
        _put_on_file(harness)

        with caplog.at_level(logging.DEBUG):
            harness["client"].post(_VERIFY)
            harness["clearinghouse"].fixture = "eligibility_271_aaa_invalid_member_id.json"
            harness["client"].post(_VERIFY)

        logged = "\n".join(record.getMessage() for record in caplog.records)
        assert _MEMBER_ID not in logged
        assert "UHC123456" not in logged
        assert "NOSUCHMEMBER" not in logged
        assert "benefitsInformation" not in logged
        assert "JANE" not in logged
