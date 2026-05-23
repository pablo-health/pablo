# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Integration tests for /api/note-types endpoints (pa-a5p.4)."""

from __future__ import annotations

from typing import Any

import pytest
from app.auth.service import get_current_user
from app.main import app
from app.notes import (
    NoteFieldDef,
    NoteSectionDef,
    NoteTypeAuthorizer,
    NoteTypeDefinition,
    NoteTypeRegistry,
    get_note_type_authorizer,
)
from app.routes.note_types import get_registry
from fastapi.testclient import TestClient


@pytest.fixture(autouse=True)
def _bypass_auth():
    """The /api/note-types endpoints require an authenticated user.

    These tests focus on the registry behavior, not the auth gate
    (covered by tests/test_route_mfa_guardrails.py), so we stub out the
    user dependency.
    """
    app.dependency_overrides[get_current_user] = object
    try:
        yield
    finally:
        app.dependency_overrides.pop(get_current_user, None)


def _registry_with(*definitions: NoteTypeDefinition) -> NoteTypeRegistry:
    registry = NoteTypeRegistry()
    for d in definitions:
        registry.register(d)
    return registry


def _sample_type(
    key: str = "sample",
    *,
    context: str = "session",
) -> NoteTypeDefinition:
    return NoteTypeDefinition(
        key=key,
        label=key.upper(),
        description=f"Sample {key}",
        tier="core",
        context=context,  # type: ignore[arg-type]
        sections=(
            NoteSectionDef(
                key="only",
                label="Only",
                fields=(
                    NoteFieldDef(
                        key="body",
                        label="Body",
                        kind="text",
                        ai_hint="body hint",
                    ),
                ),
            ),
        ),
    )


class TestListNoteTypes:
    def test_returns_all_registered_types(self) -> None:
        registry = _registry_with(_sample_type("alpha"), _sample_type("zulu"))
        app.dependency_overrides[get_registry] = lambda: registry
        try:
            response = TestClient(app).get("/api/note-types")
        finally:
            app.dependency_overrides.pop(get_registry, None)

        assert response.status_code == 200
        payload = response.json()
        assert [t["key"] for t in payload["note_types"]] == ["alpha", "zulu"]

    def test_default_registry_exposes_soap_and_narrative(self) -> None:
        response = TestClient(app).get("/api/note-types")

        assert response.status_code == 200
        keys = {t["key"] for t in response.json()["note_types"]}
        assert {"soap", "narrative"} <= keys

    def test_empty_registry_returns_empty_list(self) -> None:
        empty_registry = NoteTypeRegistry()
        app.dependency_overrides[get_registry] = lambda: empty_registry
        try:
            response = TestClient(app).get("/api/note-types")
        finally:
            app.dependency_overrides.pop(get_registry, None)

        assert response.status_code == 200
        assert response.json() == {"note_types": []}

    def test_context_filter_returns_only_matching_types(self) -> None:
        registry = _registry_with(
            _sample_type("alpha", context="session"),
            _sample_type("zulu", context="session"),
            _sample_type("safety_plan", context="patient"),
        )
        app.dependency_overrides[get_registry] = lambda: registry
        try:
            response = TestClient(app).get("/api/note-types?context=session")
        finally:
            app.dependency_overrides.pop(get_registry, None)

        assert response.status_code == 200
        keys = [t["key"] for t in response.json()["note_types"]]
        assert keys == ["alpha", "zulu"]

    def test_context_filter_excludes_non_session_types(self) -> None:
        registry = _registry_with(
            _sample_type("safety_plan", context="patient"),
        )
        app.dependency_overrides[get_registry] = lambda: registry
        try:
            response = TestClient(app).get("/api/note-types?context=session")
        finally:
            app.dependency_overrides.pop(get_registry, None)

        assert response.status_code == 200
        assert response.json() == {"note_types": []}

    def test_default_session_filter_returns_oss_session_types(self) -> None:
        response = TestClient(app).get("/api/note-types?context=session")

        assert response.status_code == 200
        keys = {t["key"] for t in response.json()["note_types"]}
        assert {"soap", "narrative"} <= keys


class TestGetNoteType:
    def test_returns_single_definition(self) -> None:
        registry = _registry_with(_sample_type("alpha"))
        app.dependency_overrides[get_registry] = lambda: registry
        try:
            response = TestClient(app).get("/api/note-types/alpha")
        finally:
            app.dependency_overrides.pop(get_registry, None)

        assert response.status_code == 200
        body: dict[str, Any] = response.json()
        assert body["key"] == "alpha"
        assert body["label"] == "ALPHA"
        assert body["tier"] == "core"
        assert body["context"] == "session"
        assert len(body["sections"]) == 1
        [section] = body["sections"]
        assert section["key"] == "only"
        [field] = section["fields"]
        assert field == {
            "key": "body",
            "label": "Body",
            "kind": "text",
            "ai_hint": "body hint",
        }

    def test_unknown_key_returns_404(self) -> None:
        registry = _registry_with(_sample_type("alpha"))
        app.dependency_overrides[get_registry] = lambda: registry
        try:
            response = TestClient(app).get("/api/note-types/does-not-exist")
        finally:
            app.dependency_overrides.pop(get_registry, None)

        assert response.status_code == 404
        assert response.json()["error"]["code"] == "NOT_FOUND"

    def test_soap_has_expected_section_shape(self) -> None:
        response = TestClient(app).get("/api/note-types/soap")

        assert response.status_code == 200
        body = response.json()
        assert body["key"] == "soap"
        assert [s["key"] for s in body["sections"]] == [
            "subjective",
            "objective",
            "assessment",
            "plan",
        ]

    def test_narrative_has_single_body_field(self) -> None:
        response = TestClient(app).get("/api/note-types/narrative")

        assert response.status_code == 200
        body = response.json()
        [section] = body["sections"]
        [field] = section["fields"]
        assert field["kind"] == "text"


class TestIsLockedField:
    """Tier-gating: the route asks the authorizer per-type.

    OSS ships an allow-all authorizer so every entry comes back unlocked;
    downstream overlays (e.g. the SaaS subscription-aware authorizer)
    flip ``is_locked`` to True for entries the caller hasn't subscribed
    to. The frontend reads this field to render an upgrade affordance
    instead of a live picker option.
    """

    def test_oss_default_authorizer_reports_everything_unlocked(self) -> None:
        registry = _registry_with(_sample_type("alpha"), _sample_type("zulu"))
        app.dependency_overrides[get_registry] = lambda: registry
        try:
            response = TestClient(app).get("/api/note-types")
        finally:
            app.dependency_overrides.pop(get_registry, None)

        assert response.status_code == 200
        assert all(t["is_locked"] is False for t in response.json()["note_types"])

    def test_overridden_authorizer_locks_specific_types(self) -> None:
        registry = _registry_with(_sample_type("alpha"), _sample_type("zulu"))

        class _LockZulu(NoteTypeAuthorizer):
            def is_allowed(self, user: object, note_type: str) -> bool:  # type: ignore[override]
                return note_type != "zulu"

        app.dependency_overrides[get_registry] = lambda: registry
        app.dependency_overrides[get_note_type_authorizer] = _LockZulu
        try:
            response = TestClient(app).get("/api/note-types")
        finally:
            app.dependency_overrides.pop(get_registry, None)
            app.dependency_overrides.pop(get_note_type_authorizer, None)

        assert response.status_code == 200
        locked = {t["key"]: t["is_locked"] for t in response.json()["note_types"]}
        assert locked == {"alpha": False, "zulu": True}

    def test_single_get_carries_is_locked(self) -> None:
        registry = _registry_with(_sample_type("alpha"))

        class _LockAlpha(NoteTypeAuthorizer):
            def is_allowed(self, user: object, note_type: str) -> bool:  # type: ignore[override]
                return False

        app.dependency_overrides[get_registry] = lambda: registry
        app.dependency_overrides[get_note_type_authorizer] = _LockAlpha
        try:
            response = TestClient(app).get("/api/note-types/alpha")
        finally:
            app.dependency_overrides.pop(get_registry, None)
            app.dependency_overrides.pop(get_note_type_authorizer, None)

        assert response.status_code == 200
        assert response.json()["is_locked"] is True
