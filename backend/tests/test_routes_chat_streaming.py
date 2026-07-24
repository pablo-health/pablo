# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""HTTP-level tests for ``POST /api/chat/conversations/{id}/messages``
(THERAPY-5x5, Phase 3 of THERAPY-bhv).

Phase 1 tests cover conversation lifecycle. This file exercises the
streaming-message endpoint: SSE event framing, model resolution
through the swappable hook, archived-conversation rejection, and the
audit row written on a safety block.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import TYPE_CHECKING
from unittest.mock import patch

from app.auth.service import (
    get_current_user_id,
    require_baa_acceptance,
    require_mfa,
)
from app.main import app
from app.models import Patient, QuotaStatus, User
from app.routes.chat import get_chat_llm_gateway, get_llm_usage_meter
from app.services.chat_llm_gateway import FakeChatLLMGateway, StreamEvent
from app.services.chat_model_resolver import get_chat_model_resolver
from fastapi import HTTPException, status

from ._streaming_body_guard import assert_no_db_checkouts

if TYPE_CHECKING:
    from app.repositories import (
        InMemoryPatientRepository,
    )
    from fastapi.testclient import TestClient


_SYSTEM_PROMPT = "You are a clinical assistant for chart QA."


def _seed_patient(patient_repo, *, user_id: str, patient_id: str = "patient-stream-1") -> Patient:
    now = datetime.now(UTC)
    patient = Patient(
        id=patient_id,
        first_name="Jane",
        last_name="Doe",
        created_at=now,
        updated_at=now,
    )
    created: Patient = patient_repo.create(patient, user_id)
    return created


def _create_conversation(client: TestClient, patient_id: str) -> str:
    response = client.post(
        "/api/chat/conversations",
        json={
            "patient_id": patient_id,
            "caller_feature_key": "chart_qa",
            "caller_system_prompt": _SYSTEM_PROMPT,
            "title": "Sleep history",
            "default_source_selection": None,
        },
    )
    assert response.status_code == 201, response.text
    conversation_id: str = response.json()["id"]
    return conversation_id


def _parse_sse(body: bytes) -> list[tuple[str, dict]]:
    """Parse ``text/event-stream`` body into ``[(event, data_json), ...]``."""
    events: list[tuple[str, dict]] = []
    blocks = body.decode().strip().split("\n\n")
    for block in blocks:
        name = ""
        data = ""
        for line in block.splitlines():
            if line.startswith("event: "):
                name = line.removeprefix("event: ").strip()
            elif line.startswith("data: "):
                data = line.removeprefix("data: ").strip()
        if name and data:
            events.append((name, json.loads(data)))
    return events


def _install_gateway(client: TestClient, gateway: FakeChatLLMGateway) -> None:
    """Wire the in-memory chat router to a FakeChatLLMGateway for one test."""
    app.dependency_overrides[get_chat_llm_gateway] = lambda: gateway


class TestSendMessage:
    def test_happy_path_streams_events(
        self,
        client: TestClient,
        mock_repo: InMemoryPatientRepository,
        mock_user_id: str,
    ) -> None:
        patient = _seed_patient(mock_repo, user_id=mock_user_id)
        conversation_id = _create_conversation(client, patient.id)
        gateway = FakeChatLLMGateway(
            script=[
                StreamEvent(delta="Hello "),
                StreamEvent(delta="there."),
                StreamEvent(finish_reason="stop", output_tokens=5),
            ]
        )
        _install_gateway(client, gateway)

        response = client.post(
            f"/api/chat/conversations/{conversation_id}/messages",
            json={"content": "ping"},
        )
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        # Contract: send_message() drains the turn generator inside the
        # route handler (j62m fix), so iterating the SSE body must do
        # zero DB pool checkouts. The guard is a no-op when the test
        # uses in-memory repos (no Engine activity), so this passes
        # vacuously here — but pins the contract for any future
        # Postgres-backed integration variant.
        with assert_no_db_checkouts("chat SSE body"):
            events = _parse_sse(response.content)
        # Subsequent uses of `response.content` are cached, fine to read.
        names = [name for name, _ in events]
        assert names[0] == "meta"
        assert names[-1] == "done"
        assert names.count("delta") == 2
        # ``done`` carries the finish_reason and output token count.
        last_event_data = events[-1][1]
        assert last_event_data["finish_reason"] == "stop"
        assert last_event_data["output_tokens"] == 5

    def test_model_override_threads_through(
        self,
        client: TestClient,
        mock_repo: InMemoryPatientRepository,
        mock_user_id: str,
    ) -> None:
        patient = _seed_patient(mock_repo, user_id=mock_user_id)
        conversation_id = _create_conversation(client, patient.id)
        gateway = FakeChatLLMGateway(script=[StreamEvent(finish_reason="stop", output_tokens=0)])
        _install_gateway(client, gateway)

        response = client.post(
            f"/api/chat/conversations/{conversation_id}/messages",
            json={"content": "ping", "model": "gemini-pro-override"},
        )
        assert response.status_code == 200
        # The fake gateway captures the model id; verify the resolver
        # honored the per-message override.
        assert gateway.calls[0]["model"] == "gemini-pro-override"

    def test_resolver_swap_takes_effect(
        self,
        client: TestClient,
        mock_repo: InMemoryPatientRepository,
        mock_user_id: str,
    ) -> None:
        patient = _seed_patient(mock_repo, user_id=mock_user_id)
        conversation_id = _create_conversation(client, patient.id)
        gateway = FakeChatLLMGateway(script=[StreamEvent(finish_reason="stop", output_tokens=0)])
        _install_gateway(client, gateway)

        def _tier_aware_resolver(*, user, feature_key, override=None):  # type: ignore[no-untyped-def]
            return override or "gemini-saas-pinned"

        app.dependency_overrides[get_chat_model_resolver] = lambda: _tier_aware_resolver

        response = client.post(
            f"/api/chat/conversations/{conversation_id}/messages",
            json={"content": "ping"},
        )
        assert response.status_code == 200
        assert gateway.calls[0]["model"] == "gemini-saas-pinned"

    def test_archived_conversation_rejected(
        self,
        client: TestClient,
        mock_repo: InMemoryPatientRepository,
        mock_user_id: str,
    ) -> None:
        patient = _seed_patient(mock_repo, user_id=mock_user_id)
        conversation_id = _create_conversation(client, patient.id)
        client.patch(
            f"/api/chat/conversations/{conversation_id}",
            json={"archive": True},
        )
        gateway = FakeChatLLMGateway(script=[StreamEvent(finish_reason="stop", output_tokens=0)])
        _install_gateway(client, gateway)

        response = client.post(
            f"/api/chat/conversations/{conversation_id}/messages",
            json={"content": "ping"},
        )
        assert response.status_code == 409
        assert gateway.calls == []

    def test_404_for_cross_user_conversation(
        self,
        client: TestClient,
        mock_repo: InMemoryPatientRepository,
        mock_user_id: str,
    ) -> None:
        # A different clinician (one without a patient_clinicians grant
        # on the conversation's patient) must get 404 — not 403, not
        # 200. We swap in a fresh chat repo whose access set is keyed
        # to a single (patient, user) pair so the foreign override
        # below has no grant.
        from app.repositories import InMemoryChatRepository  # noqa: PLC0415 — local
        from app.routes.chat import (  # noqa: PLC0415
            get_chat_repository_dep,
        )

        chat_repo_explicit = InMemoryChatRepository()
        app.dependency_overrides[get_chat_repository_dep] = lambda: chat_repo_explicit

        patient = _seed_patient(mock_repo, user_id=mock_user_id)
        # Owner grant for the chat creation call below.
        chat_repo_explicit.grant_access(patient.id, mock_user_id)
        conversation_id = _create_conversation(client, patient.id)

        other_user = User(
            id="other-user",
            email="other@example.com",
            name="Other",
            created_at=datetime(2024, 1, 1, tzinfo=UTC),
            baa_accepted_at=datetime(2024, 1, 1, tzinfo=UTC),
            baa_version="2024-01-01",
        )
        app.dependency_overrides[get_current_user_id] = lambda: "other-user"
        app.dependency_overrides[require_mfa] = lambda: {
            "uid": "other-user",
            "firebase": {},
        }
        app.dependency_overrides[require_baa_acceptance] = lambda: other_user

        gateway = FakeChatLLMGateway(script=[StreamEvent(finish_reason="stop", output_tokens=0)])
        _install_gateway(client, gateway)

        try:
            response = client.post(
                f"/api/chat/conversations/{conversation_id}/messages",
                json={"content": "ping"},
            )
            assert response.status_code == 404
            assert gateway.calls == []
        finally:
            app.dependency_overrides.pop(get_chat_repository_dep, None)

    def test_quota_exceeded_emits_error_event(
        self,
        client: TestClient,
        mock_repo: InMemoryPatientRepository,
        mock_user_id: str,
    ) -> None:
        """A meter that hard-blocks short-circuits before the gateway
        is called and surfaces a ``quota_exceeded`` SSE error event
        (THERAPY-f6eg, Phase 3b)."""
        patient = _seed_patient(mock_repo, user_id=mock_user_id)
        conversation_id = _create_conversation(client, patient.id)

        # Pin a meter that always hard-blocks. Real OSS meters always
        # return OK; this test isolates the route wiring.
        class _BlockingMeter:
            def check_quota(self, **_kwargs: object) -> QuotaStatus:
                return QuotaStatus.HARD_BLOCK

            def record_turn(self, **_kwargs: object) -> None:
                pass  # never called on a hard-block

        app.dependency_overrides[get_llm_usage_meter] = _BlockingMeter
        try:
            gateway = FakeChatLLMGateway(script=[StreamEvent(delta="never reached")])
            _install_gateway(client, gateway)

            response = client.post(
                f"/api/chat/conversations/{conversation_id}/messages",
                json={"content": "ping"},
            )
            assert response.status_code == 200
            events = _parse_sse(response.content)
            assert events[-1][0] == "error"
            assert events[-1][1]["error"] == "quota_exceeded"
            assert gateway.calls == []
        finally:
            app.dependency_overrides.pop(get_llm_usage_meter, None)

    def test_safety_block_emits_error_event(
        self,
        client: TestClient,
        mock_repo: InMemoryPatientRepository,
        mock_user_id: str,
    ) -> None:
        patient = _seed_patient(mock_repo, user_id=mock_user_id)
        conversation_id = _create_conversation(client, patient.id)
        gateway = FakeChatLLMGateway(script=[StreamEvent(finish_reason="safety")])
        _install_gateway(client, gateway)

        response = client.post(
            f"/api/chat/conversations/{conversation_id}/messages",
            json={"content": "ping"},
        )
        assert response.status_code == 200
        events = _parse_sse(response.content)
        names = [name for name, _ in events]
        assert names[0] == "meta"
        assert names[-1] == "error"
        last_event_data = events[-1][1]
        assert last_event_data["error"] == "safety_block"

    def test_request_body_validation(
        self,
        client: TestClient,
        mock_repo: InMemoryPatientRepository,
        mock_user_id: str,
    ) -> None:
        patient = _seed_patient(mock_repo, user_id=mock_user_id)
        conversation_id = _create_conversation(client, patient.id)

        # Empty content fails Pydantic min_length=1.
        response = client.post(
            f"/api/chat/conversations/{conversation_id}/messages",
            json={"content": ""},
        )
        assert response.status_code == 422

    def test_rate_limit_exceeded_returns_429(
        self,
        client: TestClient,
        mock_repo: InMemoryPatientRepository,
        mock_user_id: str,
    ) -> None:
        """A caller over the per-user burst limit gets 429 before the LLM call."""
        patient = _seed_patient(mock_repo, user_id=mock_user_id)
        conversation_id = _create_conversation(client, patient.id)

        def raise_429(key: str) -> None:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Too many requests. Please try again later.",
            )

        with patch("app.routes.chat.get_chat_send_limiter") as mock_limiter:
            mock_limiter.return_value.check.side_effect = raise_429
            response = client.post(
                f"/api/chat/conversations/{conversation_id}/messages",
                json={"content": "ping"},
            )

        assert response.status_code == 429
