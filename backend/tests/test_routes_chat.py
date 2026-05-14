# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""HTTP-level tests for /api/chat (THERAPY-tdh, Phase 1 of THERAPY-bhv).

Phase 1 only exercises conversation lifecycle (create/get/list/patch/
delete). Streaming-message tests land with Phase 3.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from app.models import Note, Patient

if TYPE_CHECKING:
    from app.repositories import (
        InMemoryChatRepository,
        InMemoryNotesRepository,
        InMemoryPatientRepository,
    )
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
        first_name=first_name,
        last_name=last_name,
        created_at=now,
        updated_at=now,
    )
    return patient_repo.create(patient, user_id)


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


# ---------------------------------------------------------------------------
# POST /conversations/preview — context manifest preview (THERAPY-0s44)
# ---------------------------------------------------------------------------


def _seed_note(
    notes_repo: InMemoryNotesRepository,
    *,
    patient_id: str,
    note_type: str,
    created_at: datetime,
    note_id: str | None = None,
) -> Note:
    note = Note(
        id=note_id or str(uuid.uuid4()),
        patient_id=patient_id,
        note_type=note_type,
        created_at=created_at,
        updated_at=created_at,
        finalized_at=created_at,
        content={"narrative": f"{note_type} body"},
    )
    notes_repo.add(note)
    return note


class TestPreviewContext:
    def test_returns_manifest_for_authorized_patient(
        self,
        client: TestClient,
        mock_repo: InMemoryPatientRepository,
        mock_notes_repo: InMemoryNotesRepository,
        mock_user_id: str,
    ) -> None:
        patient = _seed_patient(mock_repo, user_id=mock_user_id)
        intake_at = datetime(2026, 3, 3, 10, 0, tzinfo=UTC)
        soap_at = datetime(2026, 5, 9, 10, 0, tzinfo=UTC)
        _seed_note(
            mock_notes_repo,
            patient_id=patient.id,
            note_type="intake",
            created_at=intake_at,
        )
        _seed_note(
            mock_notes_repo,
            patient_id=patient.id,
            note_type="soap",
            created_at=soap_at,
        )

        response = client.post(
            "/api/chat/conversations/preview",
            json={
                "patient_id": patient.id,
                "source_selection": {
                    "most_recent_intake": True,
                    "progress_notes_recent": {"limit": 3},
                },
            },
        )
        assert response.status_code == 200
        manifest = response.json()["manifest"]
        assert manifest["patient_id"] == patient.id

        by_key = {s["source_key"]: s for s in manifest["sources_included"]}
        assert "most_recent_intake" in by_key
        assert by_key["most_recent_intake"]["row_count"] == 1
        assert by_key["most_recent_intake"]["latest_at"].startswith("2026-03-03")
        assert "progress_notes_recent" in by_key
        assert by_key["progress_notes_recent"]["row_count"] == 1
        assert by_key["progress_notes_recent"]["latest_at"].startswith("2026-05-09")

    def test_uses_design_doc_default_when_selection_omitted(
        self,
        client: TestClient,
        mock_repo: InMemoryPatientRepository,
        mock_notes_repo: InMemoryNotesRepository,
        mock_user_id: str,
    ) -> None:
        patient = _seed_patient(mock_repo, user_id=mock_user_id)
        # No notes seeded — the default selection still resolves but
        # most sources report row_count=0 / land in sources_dropped.
        response = client.post(
            "/api/chat/conversations/preview",
            json={"patient_id": patient.id},
        )
        assert response.status_code == 200
        manifest = response.json()["manifest"]
        # Default selection touches multiple sources — confirm we got
        # something back rather than asserting on the exact split,
        # which can shift as registry coverage lands.
        all_keys = {s["source_key"] for s in manifest["sources_included"]} | {
            s["source_key"] for s in manifest["sources_dropped"]
        }
        assert "most_recent_intake" in all_keys
        assert "progress_notes_recent" in all_keys

    def test_returns_404_when_patient_belongs_to_other_user(
        self,
        client: TestClient,
        mock_repo: InMemoryPatientRepository,
    ) -> None:
        _seed_patient(mock_repo, user_id="other-user", patient_id="patient-other")
        response = client.post(
            "/api/chat/conversations/preview",
            json={"patient_id": "patient-other"},
        )
        assert response.status_code == 404

    def test_returns_404_when_patient_missing(self, client: TestClient) -> None:
        response = client.post(
            "/api/chat/conversations/preview",
            json={"patient_id": "no-such-patient"},
        )
        assert response.status_code == 404

    def test_rejects_invalid_selection(
        self,
        client: TestClient,
        mock_repo: InMemoryPatientRepository,
        mock_user_id: str,
    ) -> None:
        patient = _seed_patient(mock_repo, user_id=mock_user_id)
        response = client.post(
            "/api/chat/conversations/preview",
            json={
                "patient_id": patient.id,
                # progress_notes_explicit requires {note_ids: [...]} — pass
                # the bare boolean form to trigger InvalidSelectionError.
                "source_selection": {"progress_notes_explicit": True},
            },
        )
        assert response.status_code == 422
        body = response.json()
        assert body["detail"]["error"] == "invalid_selection"

    def test_does_not_create_a_conversation(
        self,
        client: TestClient,
        mock_repo: InMemoryPatientRepository,
        mock_chat_repo: InMemoryChatRepository,
        mock_user_id: str,
    ) -> None:
        patient = _seed_patient(mock_repo, user_id=mock_user_id)
        client.post(
            "/api/chat/conversations/preview",
            json={"patient_id": patient.id},
        )
        rows, total = mock_chat_repo.list_conversations(
            patient_id=patient.id,
            owner_user_id=mock_user_id,
            caller_feature_key=None,
            include_archived=True,
            page=1,
            page_size=50,
        )
        assert total == 0
        assert rows == []

    def test_path_is_resolved_before_conversation_id_catchall(
        self,
        client: TestClient,
        mock_repo: InMemoryPatientRepository,
        mock_user_id: str,
    ) -> None:
        # Regression: ``/conversations/{conversation_id}`` GET could
        # swallow ``/conversations/preview`` if route order regresses.
        # Confirm the POST route still resolves to the preview handler.
        patient = _seed_patient(mock_repo, user_id=mock_user_id)
        response = client.post(
            "/api/chat/conversations/preview",
            json={"patient_id": patient.id},
        )
        assert response.status_code == 200
