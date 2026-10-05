# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Previewing a note type's draft from a transcript, without saving a note."""

from __future__ import annotations

import inspect
from typing import TYPE_CHECKING, Any

import pytest
from app.auth.service import get_current_user, require_baa_acceptance
from app.main import app
from app.models.audit import AuditAction, ResourceType
from app.notes import NoteTypeRegistry, get_note_type_authorizer, register_builtin_note_types
from app.notes.practice_types import RepositoryPracticeNoteTypeSource
from app.repositories import (
    InMemoryPracticeNoteTypeRepository,
    get_practice_note_type_repository,
)
from app.repositories.audit import InMemoryAuditRepository
from app.routes.note_types import get_registry, preview_note_draft
from app.routes.notes import get_note_generation_service
from app.services.audit_service import AuditService, get_audit_service
from app.services.note_generation_service import RegistryNoteGenerationService
from app.services.structured_llm_gateway import (
    FakeStructuredLLMGateway,
    StructuredCompletion,
)
from fastapi.testclient import TestClient

from .test_practice_note_types import COACH_SPEC, NOW

if TYPE_CHECKING:
    from collections.abc import Callable

    from app.models import User

TRANSCRIPT = {"format": "txt", "content": "[00:01] Therapist: Hello\n[00:03] Client: Hi"}
DRAFT = {
    "fix": {"one_thing": "Ask for referrals by minute 12."},
    "log_row": {"channels": ["email"]},
}


class _LockedAuthorizer:
    def is_allowed(self, user: Any, key: str) -> bool:
        return not key.startswith("custom.")


@pytest.fixture
def repo() -> InMemoryPracticeNoteTypeRepository:
    return InMemoryPracticeNoteTypeRepository()


@pytest.fixture
def audit_repo() -> InMemoryAuditRepository:
    return InMemoryAuditRepository()


@pytest.fixture
def gateway() -> FakeStructuredLLMGateway:
    return FakeStructuredLLMGateway(default_response=StructuredCompletion(data=DRAFT))


@pytest.fixture
def client(
    repo: InMemoryPracticeNoteTypeRepository,
    audit_repo: InMemoryAuditRepository,
    gateway: FakeStructuredLLMGateway,
    mock_user: User,
) -> Any:
    registry = NoteTypeRegistry()
    register_builtin_note_types(registry)
    registry.set_practice_source(RepositoryPracticeNoteTypeSource(lambda: repo))
    overrides: dict[Callable[..., Any], Callable[..., Any]] = {
        get_current_user: lambda: mock_user,
        require_baa_acceptance: lambda: mock_user,
        get_practice_note_type_repository: lambda: repo,
        get_registry: lambda: registry,
        get_audit_service: lambda: AuditService(audit_repo),
        get_note_generation_service: lambda: RegistryNoteGenerationService(
            registry=registry, llm_gateway=gateway
        ),
    }
    app.dependency_overrides.update(overrides)
    try:
        yield TestClient(app)
    finally:
        for dependency in overrides:
            app.dependency_overrides.pop(dependency, None)


def _preview(client: TestClient, **body: Any) -> Any:
    return client.post("/api/note-types/preview", json={"transcript": TRANSCRIPT, **body})


def test_unsaved_spec_drafts_without_being_stored(
    client: TestClient, repo: InMemoryPracticeNoteTypeRepository, gateway: FakeStructuredLLMGateway
) -> None:
    response = _preview(client, spec=COACH_SPEC, inputs={"segment": "Prescriber"})

    assert response.status_code == 200, response.text
    assert response.json() == {"key": "custom.preview", "version": None, "sections": DRAFT}
    assert "Segment: Prescriber" in gateway.calls[0]["user_prompt"]
    assert repo.list_latest() == []


def test_saved_type_drafts_at_its_version(client: TestClient) -> None:
    client.put("/api/note-types/custom/coach", json=COACH_SPEC)
    client.put("/api/note-types/custom/coach", json={**COACH_SPEC, "label": "Coach v2"})

    response = _preview(client, key="custom.coach", version=1, inputs={"segment": "Network"})

    assert response.status_code == 200, response.text
    assert response.json()["version"] == 1


def test_built_in_type_drafts(client: TestClient, gateway: FakeStructuredLLMGateway) -> None:
    gateway.default_response = StructuredCompletion(data={"note": {"body": "A short visit."}})

    response = _preview(client, key="narrative")

    assert response.status_code == 200, response.text
    assert response.json()["sections"] == {"note": {"body": "A short visit."}}


def test_audit_names_the_type_and_nothing_from_the_visit(
    client: TestClient, audit_repo: InMemoryAuditRepository
) -> None:
    _preview(client, spec=COACH_SPEC, inputs={"segment": "Prescriber"})

    (entry,) = audit_repo._entries
    assert entry.action == AuditAction.NOTE_TYPE_DRAFT_PREVIEWED.value
    assert entry.resource_type == ResourceType.NOTE_TYPE.value
    assert entry.resource_id == "custom.preview"
    recorded = repr(entry)
    assert "Hello" not in recorded
    assert "referrals" not in recorded


@pytest.mark.parametrize(
    ("body", "status_code"),
    [
        ({}, 422),
        ({"key": "narrative", "spec": COACH_SPEC}, 422),
        ({"spec": COACH_SPEC, "version": 2}, 422),
        ({"key": "narrative", "transcript": {"format": "txt", "content": "   "}}, 422),
        ({"key": "no-such-type"}, 404),
        ({"spec": COACH_SPEC}, 400),
        ({"spec": COACH_SPEC, "inputs": {"segment": "Nope"}}, 400),
        ({"key": "psychotherapy"}, 400),
    ],
    ids=[
        "neither",
        "both",
        "version-without-key",
        "empty-transcript",
        "unknown-key",
        "missing-required-input",
        "input-outside-options",
        "restricted-type",
    ],
)
def test_rejects(client: TestClient, body: dict[str, Any], status_code: int) -> None:
    response = client.post("/api/note-types/preview", json={"transcript": TRANSCRIPT, **body})

    assert response.status_code == status_code, response.text


def test_rejected_requests_draft_nothing_and_audit_nothing(
    client: TestClient, gateway: FakeStructuredLLMGateway, audit_repo: InMemoryAuditRepository
) -> None:
    _preview(client, spec=COACH_SPEC, inputs={"segment": "Nope"})

    assert gateway.calls == []
    assert audit_repo._entries == []


def test_locked_type_is_forbidden(client: TestClient) -> None:
    client.put("/api/note-types/custom/coach", json=COACH_SPEC)
    app.dependency_overrides[get_note_type_authorizer] = _LockedAuthorizer
    try:
        response = _preview(client, key="custom.coach", inputs={"segment": "Network"})
    finally:
        app.dependency_overrides.pop(get_note_type_authorizer, None)

    assert response.status_code == 403


class _ProviderBusyError(Exception):
    """A provider reply the service classes as transient (HTTP 429)."""

    code = 429


def test_transient_failure_is_503(client: TestClient, gateway: FakeStructuredLLMGateway) -> None:
    gateway.responses = [_ProviderBusyError("rate limited")]

    response = _preview(client, key="narrative")

    assert response.status_code == 503


def test_permanent_failure_is_422(client: TestClient, gateway: FakeStructuredLLMGateway) -> None:
    gateway.responses = [RuntimeError("schema mismatch")]

    response = _preview(client, key="narrative")

    assert response.status_code == 422


def test_the_route_cannot_write_a_note() -> None:
    """Structural: the route depends on no repository that stores notes,
    sessions or patients, so a preview has nowhere to write one."""
    parameters = inspect.signature(preview_note_draft).parameters
    assert set(parameters) == {
        "body",
        "request",
        "user",
        "registry",
        "authorizer",
        "generator",
        "audit",
    }
    assert NOW  # shared fixture module imported, not redefined
