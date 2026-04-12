# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Tests for pagination on list endpoints."""

from typing import Any

from app.models import TherapySession
from app.models.transcript import Transcript
from app.repositories import InMemoryTherapySessionRepository
from fastapi import status
from fastapi.testclient import TestClient


def _create_patients(client: TestClient, count: int) -> list[str]:
    """Create N patients and return their IDs."""
    ids = []
    for i in range(count):
        response = client.post(
            "/api/patients",
            json={"first_name": f"Patient{i:03d}", "last_name": f"Last{i:03d}"},
        )
        ids.append(response.json()["id"])
    return ids


def _create_sessions(
    repo: InMemoryTherapySessionRepository, user_id: str, patient_id: str, count: int
) -> None:
    """Create N sessions directly in the repo."""
    for i in range(count):
        repo.create(
            TherapySession(
                id=f"session-{i}",
                user_id=user_id,
                patient_id=patient_id,
                session_date=f"2026-01-{i + 1:02d}T10:00:00Z",
                session_number=i + 1,
                status="finalized",
                transcript=Transcript(format="txt", content="test"),
                created_at=f"2026-01-{i + 1:02d}T10:00:00Z",
            )
        )


class TestPatientListPagination:
    """Test GET /api/patients pagination."""

    def test_default_pagination(self, client: TestClient) -> None:
        _create_patients(client, 3)
        response = client.get("/api/patients")

        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert data["total"] == 3
        assert data["page"] == 1
        assert data["page_size"] == 20
        assert len(data["data"]) == 3

    def test_custom_page_size(self, client: TestClient) -> None:
        _create_patients(client, 5)
        response = client.get("/api/patients?page_size=2")

        data = response.json()
        assert data["total"] == 5
        assert data["page"] == 1
        assert data["page_size"] == 2
        assert len(data["data"]) == 2

    def test_second_page(self, client: TestClient) -> None:
        _create_patients(client, 5)
        response = client.get("/api/patients?page=2&page_size=2")

        data = response.json()
        assert data["total"] == 5
        assert data["page"] == 2
        assert data["page_size"] == 2
        assert len(data["data"]) == 2

    def test_last_page_partial(self, client: TestClient) -> None:
        _create_patients(client, 5)
        response = client.get("/api/patients?page=3&page_size=2")

        data = response.json()
        assert data["total"] == 5
        assert data["page"] == 3
        assert data["page_size"] == 2
        assert len(data["data"]) == 1

    def test_page_beyond_results_returns_empty(self, client: TestClient) -> None:
        _create_patients(client, 3)
        response = client.get("/api/patients?page=10&page_size=20")

        data = response.json()
        assert data["total"] == 3
        assert data["page"] == 10
        assert len(data["data"]) == 0

    def test_page_zero_rejected(self, client: TestClient) -> None:
        response = client.get("/api/patients?page=0")
        assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT

    def test_negative_page_rejected(self, client: TestClient) -> None:
        response = client.get("/api/patients?page=-1")
        assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT

    def test_page_size_zero_rejected(self, client: TestClient) -> None:
        response = client.get("/api/patients?page_size=0")
        assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT

    def test_page_size_over_max_rejected(self, client: TestClient) -> None:
        response = client.get("/api/patients?page_size=101")
        assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT

    def test_page_size_at_max(self, client: TestClient) -> None:
        response = client.get("/api/patients?page_size=100")
        assert response.status_code == status.HTTP_200_OK
        assert response.json()["page_size"] == 100

    def test_pagination_with_search(self, client: TestClient) -> None:
        # Create patients with different last names
        for name in ["Smith", "Smythe", "Jones", "Smithson"]:
            client.post(
                "/api/patients",
                json={"first_name": "Test", "last_name": name},
            )

        response = client.get("/api/patients?search=Smi&page_size=1")
        data = response.json()
        # "Smith", "Smithson" match (sorted); "Smythe" does not (prefix is "smy")
        assert data["total"] == 2
        assert len(data["data"]) == 1

        response = client.get("/api/patients?search=Smi&page=2&page_size=1")
        data = response.json()
        assert data["total"] == 2
        assert len(data["data"]) == 1


class TestSessionListPagination:
    """Test GET /api/sessions pagination."""

    def test_default_pagination(
        self,
        client: TestClient,
        mock_session_repo: InMemoryTherapySessionRepository,
        mock_user_id: str,
        sample_patient_data: dict[str, Any],
    ) -> None:
        create_resp = client.post("/api/patients", json=sample_patient_data)
        patient_id = create_resp.json()["id"]
        _create_sessions(mock_session_repo, mock_user_id, patient_id, 3)

        response = client.get("/api/sessions")

        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert data["total"] == 3
        assert data["page"] == 1
        assert data["page_size"] == 20
        assert len(data["data"]) == 3

    def test_custom_page_size(
        self,
        client: TestClient,
        mock_session_repo: InMemoryTherapySessionRepository,
        mock_user_id: str,
        sample_patient_data: dict[str, Any],
    ) -> None:
        create_resp = client.post("/api/patients", json=sample_patient_data)
        patient_id = create_resp.json()["id"]
        _create_sessions(mock_session_repo, mock_user_id, patient_id, 5)

        response = client.get("/api/sessions?page_size=2")

        data = response.json()
        assert data["total"] == 5
        assert data["page"] == 1
        assert data["page_size"] == 2
        assert len(data["data"]) == 2

    def test_second_page(
        self,
        client: TestClient,
        mock_session_repo: InMemoryTherapySessionRepository,
        mock_user_id: str,
        sample_patient_data: dict[str, Any],
    ) -> None:
        create_resp = client.post("/api/patients", json=sample_patient_data)
        patient_id = create_resp.json()["id"]
        _create_sessions(mock_session_repo, mock_user_id, patient_id, 5)

        response = client.get("/api/sessions?page=2&page_size=2")

        data = response.json()
        assert data["total"] == 5
        assert data["page"] == 2
        assert data["page_size"] == 2
        assert len(data["data"]) == 2

    def test_page_beyond_results_returns_empty(
        self,
        client: TestClient,
        mock_session_repo: InMemoryTherapySessionRepository,
        mock_user_id: str,
        sample_patient_data: dict[str, Any],
    ) -> None:
        create_resp = client.post("/api/patients", json=sample_patient_data)
        patient_id = create_resp.json()["id"]
        _create_sessions(mock_session_repo, mock_user_id, patient_id, 3)

        response = client.get("/api/sessions?page=10")

        data = response.json()
        assert data["total"] == 3
        assert len(data["data"]) == 0

    def test_page_zero_rejected(self, client: TestClient) -> None:
        response = client.get("/api/sessions?page=0")
        assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT

    def test_page_size_over_max_rejected(self, client: TestClient) -> None:
        response = client.get("/api/sessions?page_size=101")
        assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT

    def test_page_size_at_max(self, client: TestClient) -> None:
        response = client.get("/api/sessions?page_size=100")
        assert response.status_code == status.HTTP_200_OK
        assert response.json()["page_size"] == 100
