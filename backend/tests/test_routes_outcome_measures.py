# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""HTTP-level tests for the outcome measures API.

Covers:
- create happy-path (PHQ-9 + GAD-7 with item_scores, explicit total_score)
- validation failures (unknown instrument, out-of-range item, missing source,
  missing item_scores + total_score)
- list ordered by administered_at + instrument filter
- get one
- soft-delete then absent from list/get
- patient-access enforcement (user without access is rejected)
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from app.main import app
from app.outcome_measures.router import (
    get_outcome_measure_repository,
    get_outcome_measure_service,
)
from app.outcome_measures.service import OutcomeMeasureService
from app.repositories.outcome_measure import InMemoryOutcomeMeasureRepository
from fastapi.testclient import TestClient  # noqa: TC002 — runtime fixture type

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def outcome_repo() -> InMemoryOutcomeMeasureRepository:
    """Fresh repo with universal access open (mirrors mock_notes_repo pattern)."""
    repo = InMemoryOutcomeMeasureRepository()
    repo.grant_all_access()
    return repo


@pytest.fixture
def restricted_repo() -> InMemoryOutcomeMeasureRepository:
    """Fresh repo with NO access pre-granted (for IDOR tests)."""
    return InMemoryOutcomeMeasureRepository()


@pytest.fixture
def client_with_repo(
    client: TestClient,
    outcome_repo: InMemoryOutcomeMeasureRepository,
) -> TestClient:
    """TestClient wired to the open-access outcome repo."""
    app.dependency_overrides[get_outcome_measure_repository] = lambda: outcome_repo
    app.dependency_overrides[get_outcome_measure_service] = lambda: OutcomeMeasureService(
        outcome_repo
    )
    return client


@pytest.fixture
def restricted_client(
    client: TestClient,
    restricted_repo: InMemoryOutcomeMeasureRepository,
) -> TestClient:
    """TestClient wired to the access-controlled repo."""
    app.dependency_overrides[get_outcome_measure_repository] = lambda: restricted_repo
    app.dependency_overrides[get_outcome_measure_service] = lambda: OutcomeMeasureService(
        restricted_repo
    )
    return client


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_PATIENT_ID = str(uuid.uuid4())
_NOW = datetime.now(UTC).isoformat()
_PHQ9_ITEMS = {str(i): 1 for i in range(1, 10)}  # total = 9, mild
_GAD7_ITEMS = {str(i): 2 for i in range(1, 8)}  # total = 14, moderate


def _phq9_body(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "instrument": "phq9",
        "source": "patient_self_report",
        "administered_at": _NOW,
        "item_scores": _PHQ9_ITEMS,
    }
    base.update(overrides)
    return base


def _gad7_body(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "instrument": "gad7",
        "source": "clinician_administered_verbal",
        "administered_at": _NOW,
        "item_scores": _GAD7_ITEMS,
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# Create — happy path
# ---------------------------------------------------------------------------


class TestCreateOutcomeMeasure:
    def test_phq9_happy_path(self, client_with_repo: TestClient) -> None:
        resp = client_with_repo.post(
            f"/api/patients/{_PATIENT_ID}/outcome-measures",
            json=_phq9_body(),
        )
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body["instrument"] == "phq9"
        assert body["total_score"] == 9
        assert body["is_complete"] is True
        assert body["severity"] == "mild"
        assert body["source"] == "patient_self_report"
        assert body["patient_id"] == _PATIENT_ID

    def test_gad7_happy_path(self, client_with_repo: TestClient) -> None:
        resp = client_with_repo.post(
            f"/api/patients/{_PATIENT_ID}/outcome-measures",
            json=_gad7_body(),
        )
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body["instrument"] == "gad7"
        assert body["total_score"] == 14
        assert body["is_complete"] is True
        assert body["severity"] == "moderate"

    def test_explicit_total_score_no_items(self, client_with_repo: TestClient) -> None:
        """Accept an explicit total_score without item_scores."""
        resp = client_with_repo.post(
            f"/api/patients/{_PATIENT_ID}/outcome-measures",
            json={
                "instrument": "phq9",
                "source": "manual",
                "administered_at": _NOW,
                "total_score": 15,
            },
        )
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body["total_score"] == 15
        assert body["item_scores"] is None
        # is_complete is False when item_scores not present
        assert body["is_complete"] is False
        assert body["severity"] == "moderately severe"

    def test_with_session_and_appointment_ids(self, client_with_repo: TestClient) -> None:
        session_id = str(uuid.uuid4())
        appt_id = str(uuid.uuid4())
        resp = client_with_repo.post(
            f"/api/patients/{_PATIENT_ID}/outcome-measures",
            json=_phq9_body(session_id=session_id, appointment_id=appt_id),
        )
        assert resp.status_code == 201
        body = resp.json()
        assert body["session_id"] == session_id
        assert body["appointment_id"] == appt_id

    def test_inferred_source_accepted(self, client_with_repo: TestClient) -> None:
        resp = client_with_repo.post(
            f"/api/patients/{_PATIENT_ID}/outcome-measures",
            json=_phq9_body(source="inferred"),
        )
        assert resp.status_code == 201
        assert resp.json()["source"] == "inferred"


# ---------------------------------------------------------------------------
# Create — validation failures
# ---------------------------------------------------------------------------


class TestCreateValidationErrors:
    def test_unknown_instrument_with_items_returns_400(self, client_with_repo: TestClient) -> None:
        resp = client_with_repo.post(
            f"/api/patients/{_PATIENT_ID}/outcome-measures",
            json={
                "instrument": "bogus_scale",
                "source": "manual",
                "administered_at": _NOW,
                "item_scores": {"1": 1},
            },
        )
        assert resp.status_code == 400
        assert "UNKNOWN_INSTRUMENT" in resp.text

    def test_out_of_range_item_returns_400(self, client_with_repo: TestClient) -> None:
        bad_items = {str(i): 1 for i in range(1, 10)}
        bad_items["3"] = 5  # PHQ-9 max is 3
        resp = client_with_repo.post(
            f"/api/patients/{_PATIENT_ID}/outcome-measures",
            json=_phq9_body(item_scores=bad_items),
        )
        assert resp.status_code == 400
        assert "INVALID_ITEM_SCORES" in resp.text

    def test_missing_source_returns_422(self, client_with_repo: TestClient) -> None:
        body = _phq9_body()
        del body["source"]
        resp = client_with_repo.post(
            f"/api/patients/{_PATIENT_ID}/outcome-measures",
            json=body,
        )
        assert resp.status_code == 422

    def test_invalid_source_value_returns_422(self, client_with_repo: TestClient) -> None:
        resp = client_with_repo.post(
            f"/api/patients/{_PATIENT_ID}/outcome-measures",
            json=_phq9_body(source="invented_source"),
        )
        assert resp.status_code == 422

    def test_missing_both_items_and_total_returns_400(self, client_with_repo: TestClient) -> None:
        resp = client_with_repo.post(
            f"/api/patients/{_PATIENT_ID}/outcome-measures",
            json={
                "instrument": "phq9",
                "source": "manual",
                "administered_at": _NOW,
            },
        )
        assert resp.status_code == 400


# ---------------------------------------------------------------------------
# List
# ---------------------------------------------------------------------------


class TestListOutcomeMeasures:
    def _create(
        self,
        client: TestClient,
        patient_id: str = _PATIENT_ID,
        instrument: str = "phq9",
        minutes_ago: int = 0,
    ) -> dict[str, Any]:
        administered = (datetime.now(UTC) - timedelta(minutes=minutes_ago)).isoformat()
        body: dict[str, Any] = {
            "instrument": instrument,
            "source": "manual",
            "administered_at": administered,
            "total_score": 5,
        }
        resp = client.post(
            f"/api/patients/{patient_id}/outcome-measures",
            json=body,
        )
        assert resp.status_code == 201, resp.text
        return resp.json()

    def test_list_returns_all_measures(self, client_with_repo: TestClient) -> None:
        self._create(client_with_repo)
        self._create(client_with_repo)
        resp = client_with_repo.get(f"/api/patients/{_PATIENT_ID}/outcome-measures")
        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] == 2
        assert len(body["data"]) == 2

    def test_list_ordered_by_administered_at_ascending(self, client_with_repo: TestClient) -> None:
        # Create older first (60 min ago), then newer (0 min ago)
        self._create(client_with_repo, minutes_ago=60)
        self._create(client_with_repo, minutes_ago=0)
        resp = client_with_repo.get(f"/api/patients/{_PATIENT_ID}/outcome-measures")
        assert resp.status_code == 200
        items = resp.json()["data"]
        assert len(items) == 2
        ts0 = datetime.fromisoformat(items[0]["administered_at"])
        ts1 = datetime.fromisoformat(items[1]["administered_at"])
        assert ts0 <= ts1

    def test_list_filters_by_instrument(self, client_with_repo: TestClient) -> None:
        self._create(client_with_repo, instrument="phq9")
        self._create(client_with_repo, instrument="gad7")
        resp = client_with_repo.get(f"/api/patients/{_PATIENT_ID}/outcome-measures?instrument=phq9")
        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] == 1
        assert body["data"][0]["instrument"] == "phq9"

    def test_list_excludes_soft_deleted(self, client_with_repo: TestClient) -> None:
        created = self._create(client_with_repo)
        # soft-delete it
        del_resp = client_with_repo.delete(f"/api/outcome-measures/{created['id']}")
        assert del_resp.status_code == 204
        resp = client_with_repo.get(f"/api/patients/{_PATIENT_ID}/outcome-measures")
        assert resp.status_code == 200
        assert resp.json()["total"] == 0

    def test_list_empty_for_unknown_patient(self, client_with_repo: TestClient) -> None:
        resp = client_with_repo.get(f"/api/patients/{uuid.uuid4()}/outcome-measures")
        assert resp.status_code == 200
        assert resp.json()["total"] == 0


# ---------------------------------------------------------------------------
# Get one
# ---------------------------------------------------------------------------


class TestGetOutcomeMeasure:
    def _create(self, client: TestClient) -> dict[str, Any]:
        resp = client.post(
            f"/api/patients/{_PATIENT_ID}/outcome-measures",
            json=_phq9_body(),
        )
        assert resp.status_code == 201
        return resp.json()

    def test_get_returns_measure(self, client_with_repo: TestClient) -> None:
        created = self._create(client_with_repo)
        resp = client_with_repo.get(f"/api/outcome-measures/{created['id']}")
        assert resp.status_code == 200
        assert resp.json()["id"] == created["id"]

    def test_get_returns_404_when_missing(self, client_with_repo: TestClient) -> None:
        resp = client_with_repo.get(f"/api/outcome-measures/{uuid.uuid4()}")
        assert resp.status_code == 404

    def test_get_returns_404_after_soft_delete(self, client_with_repo: TestClient) -> None:
        created = self._create(client_with_repo)
        del_resp = client_with_repo.delete(f"/api/outcome-measures/{created['id']}")
        assert del_resp.status_code == 204
        resp = client_with_repo.get(f"/api/outcome-measures/{created['id']}")
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Soft-delete
# ---------------------------------------------------------------------------


class TestSoftDelete:
    def test_delete_returns_204(self, client_with_repo: TestClient) -> None:
        resp = client_with_repo.post(
            f"/api/patients/{_PATIENT_ID}/outcome-measures",
            json=_phq9_body(),
        )
        assert resp.status_code == 201
        measure_id = resp.json()["id"]
        del_resp = client_with_repo.delete(f"/api/outcome-measures/{measure_id}")
        assert del_resp.status_code == 204

    def test_delete_missing_returns_404(self, client_with_repo: TestClient) -> None:
        del_resp = client_with_repo.delete(f"/api/outcome-measures/{uuid.uuid4()}")
        assert del_resp.status_code == 404


# ---------------------------------------------------------------------------
# Patient-access enforcement (IDOR)
# ---------------------------------------------------------------------------


class TestPatientAccessEnforcement:
    """User A should NOT be able to read user B's patient's measures."""

    def test_list_returns_empty_for_inaccessible_patient(
        self,
        restricted_client: TestClient,
        restricted_repo: InMemoryOutcomeMeasureRepository,
    ) -> None:
        other_patient = str(uuid.uuid4())
        now = datetime.now(UTC)
        # Seed a measure directly into the repo (bypassing access check)
        restricted_repo._rows["seed-id"] = {
            "id": "seed-id",
            "patient_id": other_patient,
            "session_id": None,
            "appointment_id": None,
            "instrument": "phq9",
            "total_score": 5,
            "item_scores": None,
            "is_complete": False,
            "source": "manual",
            "item_citations": None,
            "administered_at": now,
            "created_by": "someone-else",
            "created_at": now,
            "updated_at": now,
            "deleted_at": None,
        }
        # Current user has no grant for other_patient — should get empty list
        resp = restricted_client.get(f"/api/patients/{other_patient}/outcome-measures")
        assert resp.status_code == 200
        assert resp.json()["total"] == 0

    def test_get_returns_404_for_inaccessible_measure(
        self,
        restricted_client: TestClient,
        restricted_repo: InMemoryOutcomeMeasureRepository,
    ) -> None:
        other_patient = str(uuid.uuid4())
        now = datetime.now(UTC)
        restricted_repo._rows["seed-id2"] = {
            "id": "seed-id2",
            "patient_id": other_patient,
            "session_id": None,
            "appointment_id": None,
            "instrument": "gad7",
            "total_score": 8,
            "item_scores": None,
            "is_complete": False,
            "source": "manual",
            "item_citations": None,
            "administered_at": now,
            "created_by": "someone-else",
            "created_at": now,
            "updated_at": now,
            "deleted_at": None,
        }
        # No access grant — get should return 404, not 200
        resp = restricted_client.get("/api/outcome-measures/seed-id2")
        assert resp.status_code == 404

    def test_create_returns_404_for_inaccessible_patient(
        self,
        restricted_client: TestClient,
    ) -> None:
        # Writing a measure for a patient the caller has no grant for must be
        # denied — and surfaced as 404 (not 500, and not 403 which would leak
        # patient existence). Regression guard for the missing access-denied
        # handler in the create path.
        other_patient = str(uuid.uuid4())
        resp = restricted_client.post(
            f"/api/patients/{other_patient}/outcome-measures",
            json=_phq9_body(),
        )
        assert resp.status_code == 404
