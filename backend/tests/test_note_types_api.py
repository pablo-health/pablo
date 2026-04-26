# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Integration tests for /api/note-types endpoints (pa-a5p.4)."""

from __future__ import annotations

from typing import Any

from app.main import app
from app.notes import (
    NoteFieldDef,
    NoteSectionDef,
    NoteTypeDefinition,
    NoteTypeRegistry,
)
from app.routes.note_types import get_registry
from fastapi.testclient import TestClient


def _registry_with(*definitions: NoteTypeDefinition) -> NoteTypeRegistry:
    registry = NoteTypeRegistry()
    for d in definitions:
        registry.register(d)
    return registry


def _sample_type(key: str = "sample") -> NoteTypeDefinition:
    return NoteTypeDefinition(
        key=key,
        label=key.upper(),
        description=f"Sample {key}",
        tier="core",
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
