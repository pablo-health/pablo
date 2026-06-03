# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""HTTP-level tests for the diagnostics API (PABLO-6xj.1).

Covers create (meets / not-met), the 400 error codes (unknown definition,
invalid responses, invalid code), source validation, list (order + instrument
filter + soft-delete exclusion), get, delete, the definitions endpoint, and
patient-access enforcement (IDOR).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from app.diagnostics.definition_provider import BaselineDefinitionProvider
from app.diagnostics.router import get_diagnostic_service
from app.diagnostics.service import DiagnosticService
from app.main import app
from app.repositories.diagnostic_assessment import InMemoryDiagnosticAssessmentRepository
from fastapi.testclient import TestClient  # noqa: TC002 — runtime fixture type

_PATIENT_ID = str(uuid.uuid4())
_NOW = datetime.now(UTC).isoformat()
_MET_CRITERIA = {"A1": True, "A3": True, "A4": True, "A6": True, "A8": True}
_ALL_GATES = {
    "duration": True,
    "impairment": True,
    "not_substance_medical": True,
    "not_psychotic": True,
    "no_mania_history": True,
}


@pytest.fixture
def diag_repo() -> InMemoryDiagnosticAssessmentRepository:
    repo = InMemoryDiagnosticAssessmentRepository()
    repo.grant_all_access()
    return repo


@pytest.fixture
def restricted_repo() -> InMemoryDiagnosticAssessmentRepository:
    return InMemoryDiagnosticAssessmentRepository()


@pytest.fixture
def client_with_repo(
    client: TestClient,
    diag_repo: InMemoryDiagnosticAssessmentRepository,
) -> TestClient:
    app.dependency_overrides[get_diagnostic_service] = lambda: DiagnosticService(
        diag_repo, BaselineDefinitionProvider()
    )
    return client


@pytest.fixture
def restricted_client(
    client: TestClient,
    restricted_repo: InMemoryDiagnosticAssessmentRepository,
) -> TestClient:
    app.dependency_overrides[get_diagnostic_service] = lambda: DiagnosticService(
        restricted_repo, BaselineDefinitionProvider()
    )
    return client


def _mdd_body(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "instrument": "mdd",
        "source": "manual",
        "assessed_at": _NOW,
        "criterion_responses": dict(_MET_CRITERIA),
        "gate_responses": dict(_ALL_GATES),
        "determined_icd10": "F32.9",
    }
    base.update(overrides)
    return base


class TestCreate:
    def test_meets_criteria_happy_path(self, client_with_repo: TestClient) -> None:
        resp = client_with_repo.post(
            f"/api/patients/{_PATIENT_ID}/diagnostic-assessments", json=_mdd_body()
        )
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body["instrument"] == "mdd"
        assert body["meets_criteria"] is True
        assert body["suggested_icd10"] == "F32.9"
        assert body["determined_icd10"] == "F32.9"
        assert body["diagnosis_label"] == "Major Depressive Disorder"
        assert body["confirmed_at"] is not None
        assert body["unmet_reasons"] == []

    def test_below_threshold_not_met(self, client_with_repo: TestClient) -> None:
        resp = client_with_repo.post(
            f"/api/patients/{_PATIENT_ID}/diagnostic-assessments",
            json=_mdd_body(criterion_responses={"A1": True, "A3": True}, determined_icd10=None),
        )
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body["meets_criteria"] is False
        assert body["suggested_icd10"] is None
        assert any("at least 5" in r for r in body["unmet_reasons"])

    def test_unknown_definition_returns_400(self, client_with_repo: TestClient) -> None:
        resp = client_with_repo.post(
            f"/api/patients/{_PATIENT_ID}/diagnostic-assessments",
            json=_mdd_body(instrument="bogus"),
        )
        assert resp.status_code == 400
        assert "UNKNOWN_DEFINITION" in resp.text

    def test_unknown_criterion_key_returns_400(self, client_with_repo: TestClient) -> None:
        resp = client_with_repo.post(
            f"/api/patients/{_PATIENT_ID}/diagnostic-assessments",
            json=_mdd_body(criterion_responses={"ZZ": True}),
        )
        assert resp.status_code == 400
        assert "INVALID_RESPONSES" in resp.text

    def test_code_not_in_options_returns_400(self, client_with_repo: TestClient) -> None:
        resp = client_with_repo.post(
            f"/api/patients/{_PATIENT_ID}/diagnostic-assessments",
            json=_mdd_body(determined_icd10="F99.9"),
        )
        assert resp.status_code == 400
        assert "INVALID_CODE" in resp.text

    def test_missing_source_returns_422(self, client_with_repo: TestClient) -> None:
        body = _mdd_body()
        del body["source"]
        resp = client_with_repo.post(
            f"/api/patients/{_PATIENT_ID}/diagnostic-assessments", json=body
        )
        assert resp.status_code == 422

    def test_invalid_source_returns_422(self, client_with_repo: TestClient) -> None:
        resp = client_with_repo.post(
            f"/api/patients/{_PATIENT_ID}/diagnostic-assessments",
            json=_mdd_body(source="invented"),
        )
        assert resp.status_code == 422


class TestListGetDelete:
    def _create(self, client: TestClient, instrument: str = "mdd", minutes_ago: int = 0) -> dict:
        assessed = (datetime.now(UTC) - timedelta(minutes=minutes_ago)).isoformat()
        body = _mdd_body(instrument=instrument, assessed_at=assessed)
        if instrument == "gad":
            body["criterion_responses"] = {
                "A1": True,
                "A2": True,
                "B1": True,
                "B2": True,
                "B3": True,
            }
            body["gate_responses"] = {
                "duration": True,
                "impairment": True,
                "not_substance_medical": True,
                "not_better_explained": True,
            }
            body["determined_icd10"] = "F41.1"
        resp = client.post(f"/api/patients/{_PATIENT_ID}/diagnostic-assessments", json=body)
        assert resp.status_code == 201, resp.text
        return resp.json()

    def test_list_returns_all(self, client_with_repo: TestClient) -> None:
        self._create(client_with_repo)
        self._create(client_with_repo)
        resp = client_with_repo.get(f"/api/patients/{_PATIENT_ID}/diagnostic-assessments")
        assert resp.status_code == 200
        assert resp.json()["total"] == 2

    def test_list_filters_by_instrument(self, client_with_repo: TestClient) -> None:
        self._create(client_with_repo, instrument="mdd")
        self._create(client_with_repo, instrument="gad")
        resp = client_with_repo.get(
            f"/api/patients/{_PATIENT_ID}/diagnostic-assessments?instrument=gad"
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] == 1
        assert body["data"][0]["instrument"] == "gad"

    def test_list_excludes_soft_deleted(self, client_with_repo: TestClient) -> None:
        created = self._create(client_with_repo)
        assert (
            client_with_repo.delete(f"/api/diagnostic-assessments/{created['id']}").status_code
            == 204
        )
        resp = client_with_repo.get(f"/api/patients/{_PATIENT_ID}/diagnostic-assessments")
        assert resp.json()["total"] == 0

    def test_get_returns_and_404s(self, client_with_repo: TestClient) -> None:
        created = self._create(client_with_repo)
        assert (
            client_with_repo.get(f"/api/diagnostic-assessments/{created['id']}").status_code == 200
        )
        assert (
            client_with_repo.get(f"/api/diagnostic-assessments/{uuid.uuid4()}").status_code == 404
        )

    def test_delete_missing_returns_404(self, client_with_repo: TestClient) -> None:
        assert (
            client_with_repo.delete(f"/api/diagnostic-assessments/{uuid.uuid4()}").status_code
            == 404
        )


class TestDefinitions:
    def test_list_definitions_returns_baseline(self, client_with_repo: TestClient) -> None:
        resp = client_with_repo.get("/api/diagnostic-definitions")
        assert resp.status_code == 200
        body = resp.json()
        codes = {d["code"] for d in body["data"]}
        assert {"mdd", "gad"} <= codes


class TestPatientAccessEnforcement:
    def test_create_returns_404_for_inaccessible_patient(
        self, restricted_client: TestClient
    ) -> None:
        resp = restricted_client.post(
            f"/api/patients/{uuid.uuid4()}/diagnostic-assessments", json=_mdd_body()
        )
        assert resp.status_code == 404

    def test_list_empty_for_inaccessible_patient(self, restricted_client: TestClient) -> None:
        resp = restricted_client.get(f"/api/patients/{uuid.uuid4()}/diagnostic-assessments")
        assert resp.status_code == 200
        assert resp.json()["total"] == 0
