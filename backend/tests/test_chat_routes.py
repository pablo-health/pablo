# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""End-to-end tests for the chat HTTP surface.

The tests instantiate a small FastAPI app with the chat router mounted
unconditionally and the auth chain stubbed to a known user — that
isolates the router from the global app's auto-registration of
unrelated middleware while still exercising the same dependency
graph the real app uses.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Iterator

import pytest
from app.api_errors import register_exception_handlers
from app.auth.service import (
    TenantContext,
    get_tenant_context,
    require_baa_acceptance,
)
from app.models import User
from app.models.patient import Patient
from app.repositories import (
    InMemoryChatRepository,
    InMemoryNotesRepository,
    InMemoryPatientRepository,
    InMemoryTherapySessionRepository,
)
from app.repositories.audit import InMemoryAuditRepository
from app.repositories.llm_usage import InMemoryLlmUsageRepository
from app.routes import chat as chat_route
from app.services import (
    AuditService,
    LlmUsageMeter,
    StreamedChunk,
    StreamResult,
    get_audit_service,
    get_llm_usage_meter,
)
from fastapi import FastAPI
from fastapi.testclient import TestClient


@pytest.fixture
def user() -> User:
    return User(
        id="user-1",
        email="t@example.com",
        name="T",
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
        baa_accepted_at=datetime(2026, 1, 1, tzinfo=UTC),
        baa_version="2026-01-01",
    )


@pytest.fixture
def patient_repo() -> InMemoryPatientRepository:
    repo = InMemoryPatientRepository(session_repo=InMemoryTherapySessionRepository())
    repo.create(
        Patient(
            id="pat-1",
            user_id="user-1",
            first_name="Sam",
            last_name="Lee",
            created_at=datetime(2026, 1, 1, tzinfo=UTC),
            updated_at=datetime(2026, 1, 1, tzinfo=UTC),
        )
    )
    return repo


@pytest.fixture
def chat_repo() -> InMemoryChatRepository:
    return InMemoryChatRepository()


@pytest.fixture
def audit_repo() -> InMemoryAuditRepository:
    return InMemoryAuditRepository()


@pytest.fixture
def audit_service(audit_repo: InMemoryAuditRepository) -> AuditService:
    return AuditService(audit_repo)


class _FakeGateway:
    """Streams a fixed token sequence so tests can assert SSE order."""

    def __init__(self, chunks: list[str], finish_reason: str = "stop") -> None:
        self._chunks = chunks
        self._finish_reason = finish_reason

    def stream(self, *, prompt: str, model: str) -> Iterator[StreamedChunk]:
        del prompt, model
        for c in self._chunks:
            yield StreamedChunk(text=c)

    def finish(self) -> StreamResult:
        joined = "".join(self._chunks)
        return StreamResult(
            content=joined,
            output_tokens=max(1, len(joined) // 4),
            finish_reason=self._finish_reason,
        )


@pytest.fixture
def fake_gateway() -> _FakeGateway:
    return _FakeGateway(chunks=["hello ", "world"])


@pytest.fixture
def app(
    user: User,
    patient_repo: InMemoryPatientRepository,
    chat_repo: InMemoryChatRepository,
    audit_service: AuditService,
    fake_gateway: _FakeGateway,
    monkeypatch: pytest.MonkeyPatch,
) -> FastAPI:
    notes_repo = InMemoryNotesRepository()
    meter = LlmUsageMeter(InMemoryLlmUsageRepository())

    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(chat_route.router)

    app.dependency_overrides[require_baa_acceptance] = lambda: user
    app.dependency_overrides[get_tenant_context] = lambda: TenantContext(user_id=user.id)
    app.dependency_overrides[chat_route.get_chat_repository] = lambda: chat_repo
    app.dependency_overrides[chat_route.get_notes_repository] = lambda: notes_repo
    app.dependency_overrides[chat_route.get_patient_repository] = lambda: patient_repo
    app.dependency_overrides[get_audit_service] = lambda: audit_service
    app.dependency_overrides[get_llm_usage_meter] = lambda: meter

    # `_llm_gateway_for` is a module-level helper called directly (not a
    # FastAPI dependency), so override via monkeypatch.
    monkeypatch.setattr(chat_route, "_llm_gateway_for", lambda _key: fake_gateway)

    return app


@pytest.fixture
def client(app: FastAPI) -> TestClient:
    return TestClient(app)


def _create_payload(**overrides: Any) -> dict[str, Any]:
    base = {
        "patient_id": "pat-1",
        "caller_feature_key": "chart_qa",
        "caller_system_prompt": "You are a clinical assistant.",
        "title": "Sleep history review",
    }
    base.update(overrides)
    return base


def test_create_conversation_persists_owner_and_patient(
    client: TestClient, chat_repo: InMemoryChatRepository, user: User
) -> None:
    response = client.post("/api/chat/conversations", json=_create_payload())
    assert response.status_code == 201
    body = response.json()
    assert body["patient_id"] == "pat-1"
    assert body["caller_feature_key"] == "chart_qa"
    stored = chat_repo.get_conversation(body["id"])
    assert stored is not None
    assert stored.owner_user_id == user.id
    assert stored.caller_system_prompt == "You are a clinical assistant."


def test_create_conversation_404_for_unknown_patient(client: TestClient) -> None:
    response = client.post(
        "/api/chat/conversations",
        json=_create_payload(patient_id="missing"),
    )
    assert response.status_code == 404


def test_send_message_streams_meta_delta_done_in_order(client: TestClient) -> None:
    create = client.post("/api/chat/conversations", json=_create_payload())
    conv_id = create.json()["id"]

    with client.stream(
        "POST",
        f"/api/chat/conversations/{conv_id}/messages",
        json={"content": "What is the plan?"},
    ) as stream:
        body = b"".join(stream.iter_bytes())

    text = body.decode("utf-8")
    meta_idx = text.index("event: meta")
    delta_idx = text.index("event: delta")
    done_idx = text.index("event: done")
    assert meta_idx < delta_idx < done_idx
    assert "hello " in text
    assert "world" in text


def test_get_conversation_includes_persisted_messages(
    client: TestClient,
) -> None:
    create = client.post("/api/chat/conversations", json=_create_payload())
    conv_id = create.json()["id"]
    with client.stream(
        "POST",
        f"/api/chat/conversations/{conv_id}/messages",
        json={"content": "ping"},
    ) as stream:
        for _ in stream.iter_bytes():
            pass

    response = client.get(f"/api/chat/conversations/{conv_id}")
    assert response.status_code == 200
    body = response.json()
    assert [m["role"] for m in body["messages"]] == ["user", "assistant"]
    assistant = body["messages"][1]
    assert assistant["content"] == "hello world"


def test_audit_payload_excludes_message_content(
    client: TestClient, audit_repo: InMemoryAuditRepository
) -> None:
    create = client.post("/api/chat/conversations", json=_create_payload())
    conv_id = create.json()["id"]
    secret = "patient describes anxiety from a hidden trigger"
    with client.stream(
        "POST",
        f"/api/chat/conversations/{conv_id}/messages",
        json={"content": secret},
    ) as stream:
        for _ in stream.iter_bytes():
            pass

    serialized = ""
    for entry in audit_repo._entries:  # type: ignore[attr-defined]
        serialized += str(entry.to_dict())
    assert secret not in serialized
    assert "anxiety" not in serialized


def test_send_message_blocks_on_archived_conversation(client: TestClient) -> None:
    create = client.post("/api/chat/conversations", json=_create_payload())
    conv_id = create.json()["id"]
    client.patch(
        f"/api/chat/conversations/{conv_id}",
        json={"archive": True},
    )
    response = client.post(
        f"/api/chat/conversations/{conv_id}/messages",
        json={"content": "anything"},
    )
    assert response.status_code == 409


def test_purge_conversation_cascades_messages(
    client: TestClient, chat_repo: InMemoryChatRepository
) -> None:
    create = client.post("/api/chat/conversations", json=_create_payload())
    conv_id = create.json()["id"]
    response = client.delete(f"/api/chat/conversations/{conv_id}")
    assert response.status_code == 204
    assert chat_repo.get_conversation(conv_id) is None


def test_phi_does_not_leak_into_logs(
    client: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    """The /api/chat/* surface must never emit PHI to application logs."""
    create = client.post("/api/chat/conversations", json=_create_payload())
    conv_id = create.json()["id"]
    secret = "marker-PHI-string-9c4f1e"
    with caplog.at_level(logging.DEBUG, logger="app"), client.stream(
        "POST",
        f"/api/chat/conversations/{conv_id}/messages",
        json={"content": secret},
    ) as stream:
        for _ in stream.iter_bytes():
            pass
    for record in caplog.records:
        assert secret not in record.getMessage()
