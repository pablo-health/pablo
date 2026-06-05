# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""HTTP-level tests for the medications API.

Covers:
- create 201 + response fields
- list active-first ordering
- ?status=active filter
- update status -> discontinued auto-sets stopped_at
- soft-delete 204 then absent from list
- cross-patient isolation (IDOR 404)
- 404 on unknown medication_id in PATCH/DELETE
"""

from __future__ import annotations

import uuid
from datetime import date, timedelta
from typing import Any

import pytest
from app.main import app
from app.medications.repository import InMemoryMedicationRepository
from app.medications.router import get_medication_repository, get_medication_service
from app.medications.service import MedicationService
from app.utcnow import utc_now
from fastapi.testclient import TestClient  # noqa: TC002 — runtime fixture type

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def medication_repo() -> InMemoryMedicationRepository:
    """Fresh repo with universal access open."""
    repo = InMemoryMedicationRepository()
    repo.grant_all_access()
    return repo


@pytest.fixture
def restricted_repo() -> InMemoryMedicationRepository:
    """Fresh repo with NO access pre-granted (for IDOR tests)."""
    return InMemoryMedicationRepository()


@pytest.fixture
def client_with_repo(
    client: TestClient,
    medication_repo: InMemoryMedicationRepository,
) -> TestClient:
    """TestClient wired to the open-access medication repo."""
    app.dependency_overrides[get_medication_repository] = lambda: medication_repo
    app.dependency_overrides[get_medication_service] = lambda: MedicationService(medication_repo)
    return client


@pytest.fixture
def restricted_client(
    client: TestClient,
    restricted_repo: InMemoryMedicationRepository,
) -> TestClient:
    """TestClient wired to the access-controlled repo."""
    app.dependency_overrides[get_medication_repository] = lambda: restricted_repo
    app.dependency_overrides[get_medication_service] = lambda: MedicationService(restricted_repo)
    return client


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_PATIENT_ID = str(uuid.uuid4())
_OTHER_PATIENT_ID = str(uuid.uuid4())


def _med_body(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "drug_name": "Sertraline",
        "dose": "50mg daily",
        "status": "active",
    }
    base.update(overrides)
    return base


def _create_med(
    client: TestClient,
    patient_id: str = _PATIENT_ID,
    **overrides: Any,
) -> dict[str, Any]:
    resp = client.post(
        f"/api/patients/{patient_id}/medications",
        json=_med_body(**overrides),
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


# ---------------------------------------------------------------------------
# Create
# ---------------------------------------------------------------------------


class TestCreateMedication:
    def test_create_returns_201_with_fields(self, client_with_repo: TestClient) -> None:
        resp = client_with_repo.post(
            f"/api/patients/{_PATIENT_ID}/medications",
            json=_med_body(),
        )
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body["drug_name"] == "Sertraline"
        assert body["dose"] == "50mg daily"
        assert body["status"] == "active"
        assert body["patient_id"] == _PATIENT_ID
        assert "id" in body
        assert "created_at" in body
        assert "updated_at" in body
        assert "created_by" in body
        assert body["stopped_at"] is None

    def test_create_with_started_at_and_notes(self, client_with_repo: TestClient) -> None:
        resp = client_with_repo.post(
            f"/api/patients/{_PATIENT_ID}/medications",
            json=_med_body(started_at="2026-01-15", notes="Titrating slowly"),
        )
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body["started_at"] == "2026-01-15"
        assert body["notes"] == "Titrating slowly"

    def test_create_default_status_is_active(self, client_with_repo: TestClient) -> None:
        body = {"drug_name": "Fluoxetine", "dose": "20mg daily"}
        resp = client_with_repo.post(
            f"/api/patients/{_PATIENT_ID}/medications",
            json=body,
        )
        assert resp.status_code == 201, resp.text
        assert resp.json()["status"] == "active"


# ---------------------------------------------------------------------------
# List
# ---------------------------------------------------------------------------


class TestListMedications:
    def test_list_returns_created_medications(self, client_with_repo: TestClient) -> None:
        _create_med(client_with_repo)
        _create_med(client_with_repo, drug_name="Buspirone", dose="10mg twice daily")
        resp = client_with_repo.get(f"/api/patients/{_PATIENT_ID}/medications")
        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] == 2
        assert len(body["data"]) == 2

    def test_list_active_first_ordering(self, client_with_repo: TestClient) -> None:
        """Active records appear before discontinued ones."""
        _create_med(client_with_repo, drug_name="Med A", status="discontinued")
        _create_med(client_with_repo, drug_name="Med B", status="active")
        resp = client_with_repo.get(f"/api/patients/{_PATIENT_ID}/medications")
        assert resp.status_code == 200
        items = resp.json()["data"]
        assert len(items) == 2
        assert items[0]["status"] == "active"
        assert items[1]["status"] == "discontinued"

    def test_list_status_filter(self, client_with_repo: TestClient) -> None:
        """?status=active returns only active medications."""
        _create_med(client_with_repo, drug_name="Active Med", status="active")
        _create_med(client_with_repo, drug_name="Stopped Med", status="discontinued")
        resp = client_with_repo.get(f"/api/patients/{_PATIENT_ID}/medications?status=active")
        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] == 1
        assert body["data"][0]["drug_name"] == "Active Med"

    def test_list_excludes_soft_deleted(self, client_with_repo: TestClient) -> None:
        created = _create_med(client_with_repo)
        del_resp = client_with_repo.delete(
            f"/api/patients/{_PATIENT_ID}/medications/{created['id']}"
        )
        assert del_resp.status_code == 204
        resp = client_with_repo.get(f"/api/patients/{_PATIENT_ID}/medications")
        assert resp.status_code == 200
        assert resp.json()["total"] == 0

    def test_list_empty_for_unknown_patient(self, client_with_repo: TestClient) -> None:
        resp = client_with_repo.get(f"/api/patients/{uuid.uuid4()}/medications")
        assert resp.status_code == 200
        assert resp.json()["total"] == 0


# ---------------------------------------------------------------------------
# Update
# ---------------------------------------------------------------------------


class TestUpdateMedication:
    def test_update_drug_name(self, client_with_repo: TestClient) -> None:
        created = _create_med(client_with_repo)
        resp = client_with_repo.patch(
            f"/api/patients/{_PATIENT_ID}/medications/{created['id']}",
            json={"drug_name": "Sertraline (generic)"},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["drug_name"] == "Sertraline (generic)"

    def test_update_status_to_discontinued_sets_stopped_at(
        self, client_with_repo: TestClient
    ) -> None:
        """Transitioning to discontinued without providing stopped_at auto-sets today."""
        created = _create_med(client_with_repo)
        resp = client_with_repo.patch(
            f"/api/patients/{_PATIENT_ID}/medications/{created['id']}",
            json={"status": "discontinued"},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["status"] == "discontinued"
        assert body["stopped_at"] == date.today().isoformat()

    def test_update_status_to_discontinued_with_explicit_stopped_at(
        self, client_with_repo: TestClient
    ) -> None:
        explicit_date = (date.today() - timedelta(days=7)).isoformat()
        created = _create_med(client_with_repo)
        resp = client_with_repo.patch(
            f"/api/patients/{_PATIENT_ID}/medications/{created['id']}",
            json={"status": "discontinued", "stopped_at": explicit_date},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["stopped_at"] == explicit_date

    def test_update_discontinued_with_stop_reason(
        self, client_with_repo: TestClient
    ) -> None:
        """A free-text reason can be recorded when a medication is stopped."""
        created = _create_med(client_with_repo)
        resp = client_with_repo.patch(
            f"/api/patients/{_PATIENT_ID}/medications/{created['id']}",
            json={"status": "discontinued", "stop_reason": "side effects"},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["status"] == "discontinued"
        assert body["stop_reason"] == "side effects"

    def test_create_stop_reason_defaults_to_none(
        self, client_with_repo: TestClient
    ) -> None:
        assert _create_med(client_with_repo)["stop_reason"] is None

    def test_update_started_at(self, client_with_repo: TestClient) -> None:
        """A medication's start date can be edited after creation."""
        created = _create_med(client_with_repo)
        new_date = "2026-02-20"
        resp = client_with_repo.patch(
            f"/api/patients/{_PATIENT_ID}/medications/{created['id']}",
            json={"started_at": new_date},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["started_at"] == new_date

    def test_update_unknown_id_returns_404(self, client_with_repo: TestClient) -> None:
        resp = client_with_repo.patch(
            f"/api/patients/{_PATIENT_ID}/medications/{uuid.uuid4()}",
            json={"drug_name": "Ghost"},
        )
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Soft-delete
# ---------------------------------------------------------------------------


class TestSoftDelete:
    def test_delete_returns_204(self, client_with_repo: TestClient) -> None:
        created = _create_med(client_with_repo)
        resp = client_with_repo.delete(f"/api/patients/{_PATIENT_ID}/medications/{created['id']}")
        assert resp.status_code == 204

    def test_delete_then_absent_from_list(self, client_with_repo: TestClient) -> None:
        created = _create_med(client_with_repo)
        client_with_repo.delete(f"/api/patients/{_PATIENT_ID}/medications/{created['id']}")
        resp = client_with_repo.get(f"/api/patients/{_PATIENT_ID}/medications")
        ids = [m["id"] for m in resp.json()["data"]]
        assert created["id"] not in ids

    def test_delete_unknown_id_returns_404(self, client_with_repo: TestClient) -> None:
        resp = client_with_repo.delete(f"/api/patients/{_PATIENT_ID}/medications/{uuid.uuid4()}")
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Cross-patient isolation (IDOR)
# ---------------------------------------------------------------------------


class TestPatientAccessEnforcement:
    """A user without access to a patient's medications should get 404 / empty list."""

    def test_list_returns_empty_for_inaccessible_patient(
        self,
        restricted_client: TestClient,
        restricted_repo: InMemoryMedicationRepository,
    ) -> None:
        other_patient = str(uuid.uuid4())
        now = utc_now()
        # Seed a medication directly into the repo (bypassing access check)
        restricted_repo._rows["seed-med-1"] = {
            "id": "seed-med-1",
            "patient_id": other_patient,
            "drug_name": "Hidden Med",
            "dose": "10mg",
            "status": "active",
            "started_at": None,
            "stopped_at": None,
            "notes": None,
            "created_by": "someone-else",
            "created_at": now,
            "updated_at": now,
            "deleted_at": None,
        }
        # Current user has no grant for other_patient — should get empty list
        resp = restricted_client.get(f"/api/patients/{other_patient}/medications")
        assert resp.status_code == 200
        assert resp.json()["total"] == 0

    def test_create_returns_404_for_inaccessible_patient(
        self, restricted_client: TestClient
    ) -> None:
        """Writing a medication for an inaccessible patient must return 404."""
        other_patient = str(uuid.uuid4())
        resp = restricted_client.post(
            f"/api/patients/{other_patient}/medications",
            json=_med_body(),
        )
        assert resp.status_code == 404

    def test_update_returns_404_for_inaccessible_medication(
        self,
        restricted_client: TestClient,
        restricted_repo: InMemoryMedicationRepository,
    ) -> None:
        other_patient = str(uuid.uuid4())
        now = utc_now()
        restricted_repo._rows["seed-med-2"] = {
            "id": "seed-med-2",
            "patient_id": other_patient,
            "drug_name": "Secret Med",
            "dose": "5mg",
            "status": "active",
            "started_at": None,
            "stopped_at": None,
            "notes": None,
            "created_by": "someone-else",
            "created_at": now,
            "updated_at": now,
            "deleted_at": None,
        }
        resp = restricted_client.patch(
            f"/api/patients/{other_patient}/medications/seed-med-2",
            json={"drug_name": "Hacked"},
        )
        assert resp.status_code == 404

    def test_delete_returns_404_for_inaccessible_medication(
        self,
        restricted_client: TestClient,
        restricted_repo: InMemoryMedicationRepository,
    ) -> None:
        other_patient = str(uuid.uuid4())
        now = utc_now()
        restricted_repo._rows["seed-med-3"] = {
            "id": "seed-med-3",
            "patient_id": other_patient,
            "drug_name": "Secret Med",
            "dose": "5mg",
            "status": "active",
            "started_at": None,
            "stopped_at": None,
            "notes": None,
            "created_by": "someone-else",
            "created_at": now,
            "updated_at": now,
            "deleted_at": None,
        }
        resp = restricted_client.delete(f"/api/patients/{other_patient}/medications/seed-med-3")
        assert resp.status_code == 404
