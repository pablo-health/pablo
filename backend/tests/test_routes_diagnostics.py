# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""HTTP-level tests for the diagnostics API (PABLO-6xj.1).

Covers create (meets / not-met), the 400 error codes (unknown definition,
invalid responses, invalid code), source validation, list (order + instrument
filter + soft-delete exclusion), get, delete, the definitions endpoint, and
patient-access enforcement (IDOR). Uses synthetic, non-clinical definitions.
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

from .diagnostics_fixtures import (
    SYNTHETIC2_ALL_GATES,
    SYNTHETIC2_MET_CRITERIA,
    SYNTHETIC_ALL_GATES,
    SYNTHETIC_DEFINITIONS,
    SYNTHETIC_MET_CRITERIA,
)

_PATIENT_ID = str(uuid.uuid4())
_NOW = datetime.now(UTC).isoformat()


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
        diag_repo, BaselineDefinitionProvider(SYNTHETIC_DEFINITIONS)
    )
    return client


@pytest.fixture
def restricted_client(
    client: TestClient,
    restricted_repo: InMemoryDiagnosticAssessmentRepository,
) -> TestClient:
    app.dependency_overrides[get_diagnostic_service] = lambda: DiagnosticService(
        restricted_repo, BaselineDefinitionProvider(SYNTHETIC_DEFINITIONS)
    )
    return client


def _body(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "instrument": "synthetic",
        "source": "manual",
        "assessed_at": _NOW,
        "criterion_responses": dict(SYNTHETIC_MET_CRITERIA),
        "gate_responses": dict(SYNTHETIC_ALL_GATES),
        "determined_icd10": "T00.1",
    }
    base.update(overrides)
    return base


class TestCreate:
    def test_meets_criteria_happy_path(self, client_with_repo: TestClient) -> None:
        resp = client_with_repo.post(
            f"/api/patients/{_PATIENT_ID}/diagnostic-assessments", json=_body()
        )
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body["instrument"] == "synthetic"
        assert body["meets_criteria"] is True
        assert body["suggested_icd10"] == "T00.1"
        assert body["determined_icd10"] == "T00.1"
        assert body["diagnosis_label"] == "Synthetic Screen"
        assert body["confirmed_at"] is not None
        assert body["unmet_reasons"] == []

    def test_below_threshold_not_met(self, client_with_repo: TestClient) -> None:
        resp = client_with_repo.post(
            f"/api/patients/{_PATIENT_ID}/diagnostic-assessments",
            json=_body(criterion_responses={"A1": True, "B1": True}, determined_icd10=None),
        )
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body["meets_criteria"] is False
        assert body["suggested_icd10"] is None
        assert any("at least 2" in r for r in body["unmet_reasons"])

    def test_unknown_definition_returns_400(self, client_with_repo: TestClient) -> None:
        resp = client_with_repo.post(
            f"/api/patients/{_PATIENT_ID}/diagnostic-assessments",
            json=_body(instrument="bogus"),
        )
        assert resp.status_code == 400
        assert "UNKNOWN_DEFINITION" in resp.text

    def test_unknown_criterion_key_returns_400(self, client_with_repo: TestClient) -> None:
        resp = client_with_repo.post(
            f"/api/patients/{_PATIENT_ID}/diagnostic-assessments",
            json=_body(criterion_responses={"ZZ": True}),
        )
        assert resp.status_code == 400
        assert "INVALID_RESPONSES" in resp.text

    def test_code_not_in_options_returns_400(self, client_with_repo: TestClient) -> None:
        resp = client_with_repo.post(
            f"/api/patients/{_PATIENT_ID}/diagnostic-assessments",
            json=_body(determined_icd10="F99.9"),
        )
        assert resp.status_code == 400
        assert "INVALID_CODE" in resp.text

    def test_missing_source_returns_422(self, client_with_repo: TestClient) -> None:
        body = _body()
        del body["source"]
        resp = client_with_repo.post(
            f"/api/patients/{_PATIENT_ID}/diagnostic-assessments", json=body
        )
        assert resp.status_code == 422

    def test_invalid_source_returns_422(self, client_with_repo: TestClient) -> None:
        resp = client_with_repo.post(
            f"/api/patients/{_PATIENT_ID}/diagnostic-assessments",
            json=_body(source="invented"),
        )
        assert resp.status_code == 422


class TestListGetDelete:
    def _create(
        self, client: TestClient, instrument: str = "synthetic", minutes_ago: int = 0
    ) -> dict:
        assessed = (datetime.now(UTC) - timedelta(minutes=minutes_ago)).isoformat()
        body = _body(instrument=instrument, assessed_at=assessed)
        if instrument == "synthetic2":
            body["criterion_responses"] = dict(SYNTHETIC2_MET_CRITERIA)
            body["gate_responses"] = dict(SYNTHETIC2_ALL_GATES)
            body["determined_icd10"] = "T01.1"
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
        self._create(client_with_repo, instrument="synthetic")
        self._create(client_with_repo, instrument="synthetic2")
        resp = client_with_repo.get(
            f"/api/patients/{_PATIENT_ID}/diagnostic-assessments?instrument=synthetic2"
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] == 1
        assert body["data"][0]["instrument"] == "synthetic2"

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
    def test_list_definitions_returns_provider_content(self, client_with_repo: TestClient) -> None:
        resp = client_with_repo.get("/api/diagnostic-definitions")
        assert resp.status_code == 200
        body = resp.json()
        codes = {d["code"] for d in body["data"]}
        assert {"synthetic", "synthetic2"} <= codes


class TestPrescribingSupport:
    def test_empty_by_default(self, client_with_repo: TestClient) -> None:
        # A definition without prescribing-support data returns empty/None.
        resp = client_with_repo.get("/api/diagnostic-definitions/synthetic/prescribing-support")
        assert resp.status_code == 200
        body = resp.json()
        assert body["code"] == "synthetic"
        assert body["differentials"] == []
        assert body["prescribing_safeguards"] == []
        assert body["medication_rationale"] is None

    def test_returns_populated_support_data(self, client_with_repo: TestClient) -> None:
        resp = client_with_repo.get("/api/diagnostic-definitions/synthetic_rx/prescribing-support")
        assert resp.status_code == 200
        body = resp.json()
        assert body["code"] == "synthetic_rx"

        assert len(body["differentials"]) == 1
        diff = body["differentials"][0]
        assert diff["icd_code"] == "T99.0"
        assert len(diff["transcript_cues"]) == 2
        assert diff["transcript_cues"][0]["citation"] == "Placeholder 2026"
        assert diff["transcript_cues"][1]["citation"] is None

        assert body["prescribing_safeguards"][0]["key"] == "registry_check"

        rationale = body["medication_rationale"]
        assert rationale["first_line"] == ["agent-a", "agent-b"]
        assert rationale["alternatives"] == ["agent-c"]
        assert rationale["this_agent_now"].startswith("agent-a")
        assert rationale["citations"] == ["Placeholder guideline 2026"]

    def test_unknown_code_returns_404(self, client_with_repo: TestClient) -> None:
        resp = client_with_repo.get("/api/diagnostic-definitions/nope/prescribing-support")
        assert resp.status_code == 404


class TestPatientAccessEnforcement:
    def test_create_returns_404_for_inaccessible_patient(
        self, restricted_client: TestClient
    ) -> None:
        resp = restricted_client.post(
            f"/api/patients/{uuid.uuid4()}/diagnostic-assessments", json=_body()
        )
        assert resp.status_code == 404

    def test_list_empty_for_inaccessible_patient(self, restricted_client: TestClient) -> None:
        resp = restricted_client.get(f"/api/patients/{uuid.uuid4()}/diagnostic-assessments")
        assert resp.status_code == 200
        assert resp.json()["total"] == 0
