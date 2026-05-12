# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""HTTP-level tests for /api/chat (THERAPY-tdh, Phase 1 of THERAPY-bhv).

Phase 1 only exercises conversation lifecycle (create/get/list/patch/
delete). Streaming-message tests land with Phase 3.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from app.models import Patient

if TYPE_CHECKING:
    from app.repositories import InMemoryChatRepository, InMemoryPatientRepository
    from fastapi.testclient import TestClient

_SYSTEM_PROMPT = "You are a clinical assistant for chart QA."


def _seed_patient(
    patient_repo: InMemoryPatientRepository,
    *,
    user_id: str,
    patient_id: str = "patient-1",
    first_name: str = "Jane",
    last_name: str = "Doe",
) -> Patient:
    now = datetime.now(UTC)
    patient = Patient(
        id=patient_id,
        user_id=user_id,
        first_name=first_name,
        last_name=last_name,
        created_at=now,
        updated_at=now,
    )
    return patient_repo.create(patient)


def _create_payload(
    patient_id: str,
    *,
    title: str | None = "Sleep history review",
    feature_key: str = "chart_qa",
) -> dict:
    return {
        "patient_id": patient_id,
        "caller_feature_key": feature_key,
        "caller_system_prompt": _SYSTEM_PROMPT,
        "title": title,
        "default_source_selection": {"current_medications": True},
    }


class TestCreateConversation:
    def test_creates_with_authorized_patient(
        self,
        client: TestClient,
        mock_repo: InMemoryPatientRepository,
        mock_user_id: str,
    ) -> None:
        patient = _seed_patient(mock_repo, user_id=mock_user_id)
        response = client.post("/api/chat/conversations", json=_create_payload(patient.id))
        assert response.status_code == 201
        body = response.json()
        assert body["patient_id"] == patient.id
        assert body["owner_user_id"] == mock_user_id
        assert body["caller_feature_key"] == "chart_qa"
        assert body["title"] == "Sleep history review"
        assert body["archived_at"] is None
        assert body["last_turn_at"] is None
        assert body["default_source_selection"] == {"current_medications": True}

    def test_seeds_default_title_when_omitted(
        self,
        client: TestClient,
        mock_repo: InMemoryPatientRepository,
        mock_user_id: str,
    ) -> None:
        patient = _seed_patient(mock_repo, user_id=mock_user_id)
        response = client.post(
            "/api/chat/conversations",
            json=_create_payload(patient.id, title=None),
        )
        assert response.status_code == 201
        # Falls back to "Chat about <patient_name>" per the design doc.
        assert response.json()["title"] == "Chat about Jane Doe"

    def test_returns_404_when_patient_missing(self, client: TestClient) -> None:
        response = client.post(
            "/api/chat/conversations",
            json=_create_payload("does-not-exist"),
        )
        assert response.status_code == 404

    def test_returns_404_when_patient_belongs_to_other_user(
        self,
        client: TestClient,
        mock_repo: InMemoryPatientRepository,
    ) -> None:
        # Seeded under a different owner — the in-memory repo's get()
        # rejects on user_id mismatch, mirroring production behavior.
        _seed_patient(mock_repo, user_id="someone-else", patient_id="patient-other")
        response = client.post("/api/chat/conversations", json=_create_payload("patient-other"))
        assert response.status_code == 404

    def test_rejects_oversized_system_prompt(
        self,
        client: TestClient,
        mock_repo: InMemoryPatientRepository,
        mock_user_id: str,
    ) -> None:
        patient = _seed_patient(mock_repo, user_id=mock_user_id)
        payload = _create_payload(patient.id)
        payload["caller_system_prompt"] = "x" * 16_385
        response = client.post("/api/chat/conversations", json=payload)
        assert response.status_code == 422


class TestGetConversation:
    def test_returns_conversation_with_empty_messages(
        self,
        client: TestClient,
        mock_repo: InMemoryPatientRepository,
        mock_user_id: str,
    ) -> None:
        patient = _seed_patient(mock_repo, user_id=mock_user_id)
        create = client.post("/api/chat/conversations", json=_create_payload(patient.id))
        conv_id = create.json()["id"]

        response = client.get(f"/api/chat/conversations/{conv_id}")
        assert response.status_code == 200
        body = response.json()
        assert body["id"] == conv_id
        assert body["messages"] == []

    def test_returns_404_for_unknown_conversation(self, client: TestClient) -> None:
        response = client.get("/api/chat/conversations/does-not-exist")
        assert response.status_code == 404


class TestListConversations:
    def test_lists_active_for_owner(
        self,
        client: TestClient,
        mock_repo: InMemoryPatientRepository,
        mock_user_id: str,
    ) -> None:
        patient = _seed_patient(mock_repo, user_id=mock_user_id)
        for i in range(3):
            client.post(
                "/api/chat/conversations",
                json=_create_payload(patient.id, title=f"Conv {i}"),
            )
        response = client.get("/api/chat/conversations", params={"patient_id": patient.id})
        assert response.status_code == 200
        body = response.json()
        assert body["total"] == 3
        assert len(body["data"]) == 3

    def test_filters_by_caller_feature_key(
        self,
        client: TestClient,
        mock_repo: InMemoryPatientRepository,
        mock_user_id: str,
    ) -> None:
        patient = _seed_patient(mock_repo, user_id=mock_user_id)
        client.post(
            "/api/chat/conversations",
            json=_create_payload(patient.id, feature_key="chart_qa"),
        )
        client.post(
            "/api/chat/conversations",
            json=_create_payload(patient.id, feature_key="rx_justification_workspace"),
        )
        response = client.get(
            "/api/chat/conversations",
            params={"patient_id": patient.id, "caller_feature_key": "chart_qa"},
        )
        assert response.status_code == 200
        body = response.json()
        assert body["total"] == 1
        assert body["data"][0]["caller_feature_key"] == "chart_qa"

    def test_excludes_archived_by_default(
        self,
        client: TestClient,
        mock_repo: InMemoryPatientRepository,
        mock_user_id: str,
    ) -> None:
        patient = _seed_patient(mock_repo, user_id=mock_user_id)
        create = client.post("/api/chat/conversations", json=_create_payload(patient.id))
        conv_id = create.json()["id"]
        client.patch(f"/api/chat/conversations/{conv_id}", json={"archive": True})

        default = client.get("/api/chat/conversations", params={"patient_id": patient.id})
        assert default.json()["total"] == 0

        included = client.get(
            "/api/chat/conversations",
            params={"patient_id": patient.id, "include_archived": "true"},
        )
        assert included.json()["total"] == 1


class TestUpdateConversation:
    def test_updates_title(
        self,
        client: TestClient,
        mock_repo: InMemoryPatientRepository,
        mock_user_id: str,
    ) -> None:
        patient = _seed_patient(mock_repo, user_id=mock_user_id)
        create = client.post("/api/chat/conversations", json=_create_payload(patient.id))
        conv_id = create.json()["id"]

        response = client.patch(f"/api/chat/conversations/{conv_id}", json={"title": "Renamed"})
        assert response.status_code == 200
        assert response.json()["title"] == "Renamed"

    def test_archive_sets_timestamp(
        self,
        client: TestClient,
        mock_repo: InMemoryPatientRepository,
        mock_user_id: str,
    ) -> None:
        patient = _seed_patient(mock_repo, user_id=mock_user_id)
        create = client.post("/api/chat/conversations", json=_create_payload(patient.id))
        conv_id = create.json()["id"]

        response = client.patch(f"/api/chat/conversations/{conv_id}", json={"archive": True})
        assert response.status_code == 200
        assert response.json()["archived_at"] is not None

    def test_unarchive_clears_timestamp(
        self,
        client: TestClient,
        mock_repo: InMemoryPatientRepository,
        mock_user_id: str,
    ) -> None:
        patient = _seed_patient(mock_repo, user_id=mock_user_id)
        create = client.post("/api/chat/conversations", json=_create_payload(patient.id))
        conv_id = create.json()["id"]
        client.patch(f"/api/chat/conversations/{conv_id}", json={"archive": True})

        response = client.patch(f"/api/chat/conversations/{conv_id}", json={"archive": False})
        assert response.status_code == 200
        assert response.json()["archived_at"] is None

    def test_rejects_unknown_conversation(self, client: TestClient) -> None:
        response = client.patch("/api/chat/conversations/does-not-exist", json={"title": "x"})
        assert response.status_code == 404


class TestDeleteConversation:
    def test_default_mode_purges(
        self,
        client: TestClient,
        mock_repo: InMemoryPatientRepository,
        mock_user_id: str,
        mock_chat_repo: InMemoryChatRepository,
    ) -> None:
        patient = _seed_patient(mock_repo, user_id=mock_user_id)
        create = client.post("/api/chat/conversations", json=_create_payload(patient.id))
        conv_id = create.json()["id"]

        response = client.delete(f"/api/chat/conversations/{conv_id}")
        assert response.status_code == 204
        assert mock_chat_repo.get_conversation(conv_id) is None

    def test_archive_mode_keeps_row(
        self,
        client: TestClient,
        mock_repo: InMemoryPatientRepository,
        mock_user_id: str,
        mock_chat_repo: InMemoryChatRepository,
    ) -> None:
        patient = _seed_patient(mock_repo, user_id=mock_user_id)
        create = client.post("/api/chat/conversations", json=_create_payload(patient.id))
        conv_id = create.json()["id"]

        response = client.delete(f"/api/chat/conversations/{conv_id}", params={"mode": "archive"})
        assert response.status_code == 204

        # Row remains, archived_at populated.
        conv = mock_chat_repo.get_conversation(conv_id)
        assert conv is not None
        assert conv.archived_at is not None

    def test_rejects_unknown_conversation(self, client: TestClient) -> None:
        response = client.delete("/api/chat/conversations/does-not-exist")
        assert response.status_code == 404
