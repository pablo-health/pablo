# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""A built-in detached into a practice's own full type keeps its chart-fed fields.

Detaching resolves a based type to a full spec and saves that spec as the
practice's own. Each field the built-in prints from the chart names its
source on the spec, so the copy must carry it through the resolve, the save
and the read back; otherwise the copy asks the model to draft those fields
from their hints. No model runs here: a scripted gateway answers every call.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any

import pytest
from app.auth.service import get_current_user, require_baa_acceptance
from app.main import app
from app.models import Patient, Transcript
from app.notes import NoteTypeRegistry, register_builtin_note_types
from app.notes.chart_context import ChartContext
from app.notes.chart_fields import rendered_fields
from app.notes.practice_types import RepositoryPracticeNoteTypeSource
from app.repositories import (
    InMemoryPracticeNoteTypeRepository,
    get_practice_note_type_repository,
)
from app.routes.note_types import get_registry
from app.services.chart_field_extraction import SCHEMA_TITLE as EXTRACTION_TITLE
from app.services.note_generation_service import RegistryNoteGenerationService
from app.services.structured_llm_gateway import StructuredCompletion, StructuredLLMGateway
from fastapi.testclient import TestClient

if TYPE_CHECKING:
    from collections.abc import Iterator

BUILT_IN = "psychiatric_follow_up"
COPY = "custom.our_follow_up"
NOW = datetime(2026, 10, 9, 15, tzinfo=UTC)
PATIENT = Patient(id="p", first_name="", last_name="", created_at=NOW, updated_at=NOW)
DRAFTED = "Drafted by the model."


@dataclass
class _ScriptedGateway(StructuredLLMGateway):
    """Says nothing in the extraction and fills every drafted string; records each call."""

    calls: list[dict[str, Any]] = field(default_factory=list)
    lock: threading.Lock = field(default_factory=threading.Lock)

    def complete_structured(self, **kwargs: Any) -> StructuredCompletion:
        with self.lock:
            self.calls.append(kwargs)
        schema = kwargs["response_schema"]
        if schema.get("title") == EXTRACTION_TITLE:
            return StructuredCompletion(data={"statements": [], "diagnoses": []})
        return StructuredCompletion(data=_stand_in(schema))


def _stand_in(schema: dict[str, Any]) -> Any:
    kind = schema.get("type")
    if kind == "object":
        return {k: _stand_in(v) for k, v in schema.get("properties", {}).items()}
    if kind == "array":
        return []
    return DRAFTED if kind == "string" else None


def _asked(gateway: _ScriptedGateway) -> set[tuple[str, str]]:
    """Every (section, field) any drafting call asked the model for."""
    return {
        (section, key)
        for call in gateway.calls
        if call["response_schema"].get("title") != EXTRACTION_TITLE
        for section, body in call["response_schema"].get("properties", {}).items()
        if isinstance(body, dict)
        for key in body.get("properties", {})
    }


def _chart_fed(definition: Any) -> dict[tuple[str, str], str]:
    return {(r.section, r.field.key): r.source for r in rendered_fields(definition)}


@pytest.fixture
def registry() -> NoteTypeRegistry:
    return NoteTypeRegistry()


@pytest.fixture
def client(registry: NoteTypeRegistry) -> Iterator[TestClient]:
    repo = InMemoryPracticeNoteTypeRepository()
    register_builtin_note_types(registry)
    registry.set_practice_source(RepositoryPracticeNoteTypeSource(lambda: repo, registry.base_for))
    user = SimpleNamespace(id="00000000-0000-0000-0000-000000000001")
    app.dependency_overrides[get_current_user] = lambda: user
    app.dependency_overrides[require_baa_acceptance] = lambda: user
    app.dependency_overrides[get_practice_note_type_repository] = lambda: repo
    app.dependency_overrides[get_registry] = lambda: registry
    try:
        yield TestClient(app)
    finally:
        for dependency in (
            get_current_user,
            require_baa_acceptance,
            get_practice_note_type_repository,
            get_registry,
        ):
            app.dependency_overrides.pop(dependency, None)


def _detach(client: TestClient) -> dict[str, Any]:
    """Start from the built-in, detach it, and save the result as the practice's own."""
    based = {"label": "Our follow-up", "base": BUILT_IN, "patch": {}}
    resolved = client.post("/api/note-types/resolve", json=based)
    assert resolved.status_code == 200, resolved.text
    spec: dict[str, Any] = resolved.json()["spec"]
    saved = client.put(f"/api/note-types/custom/{COPY.removeprefix('custom.')}", json=spec)
    assert saved.status_code == 200, saved.text
    assert saved.json()["based_on"] is None
    return spec


def test_a_detached_copy_keeps_every_chart_fed_field_and_its_source(
    client: TestClient, registry: NoteTypeRegistry
) -> None:
    _detach(client)

    built_in = _chart_fed(registry.get(BUILT_IN))
    assert built_in, "the built-in prints fields from the chart"
    assert _chart_fed(registry.get(COPY)) == built_in
    stored = client.get(f"/api/note-types/{COPY}").json()
    sources = {
        (s["key"], f["key"]): f["source"]
        for s in stored["spec"]["sections"]
        for f in s["fields"]
        if "source" in f
    }
    assert sources == built_in


def test_a_detached_copy_drafts_none_of_them_and_prints_the_charts_allergy(
    client: TestClient, registry: NoteTypeRegistry
) -> None:
    _detach(client)
    definition = registry.get(COPY)
    gateway = _ScriptedGateway()
    service = RegistryNoteGenerationService(llm_gateway=gateway, model="scripted")

    content = service.generate_note(
        COPY,
        Transcript(format="txt", content="[00:01] Client: Sleep is a little better."),
        PATIENT,
        NOW,
        inputs={"place_of_service": "In office"},
        definition=definition,
        chart=ChartContext(
            allergy_status="recorded", allergies=({"substance": "Sulfa", "reaction": "rash"},)
        ),
        client_present_end_seconds=0,
    ).content

    asked = _asked(gateway)
    assert asked, "the drafted fields still go to the model"
    assert not asked & set(_chart_fed(definition))
    assert ("subjective", "chief_complaint") in asked
    assert content["medications"]["allergies"] == "Sulfa (rash)"
    assert content["subjective"]["chief_complaint"] == DRAFTED
