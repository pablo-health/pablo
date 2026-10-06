# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""The end-to-end stack's stand-in answering note-type derive calls.

Runs the real derive service against the stand-in's app, so the proposal
and each sample's extraction are validated exactly as a model's would be.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest
from app.routes.note_type_derive import get_note_type_derive_service
from app.services import http_structured_llm_gateway
from app.services.http_structured_llm_gateway import HttpStructuredLLMGateway
from app.services.note_import_service import NoteImportService
from app.services.note_type_derive_service import NoteTypeDeriveService
from app.settings import Settings
from fastapi.testclient import TestClient

from scripts.fake_llm import DERIVED_PROPOSAL
from scripts.fake_llm import app as fake_llm_app

if TYPE_CHECKING:
    import httpx

BASE_URL = "http://fake-llm:8083/notes"

STRAY = "The client brought a drawing from a weekend art class to show."
SAMPLE = (
    "Interval history: Sleeping better since the last visit, appetite steady.\n"
    "Current medications: sertraline 50 mg daily\n"
    "Follow up: Return in four weeks for a medication check.\n"
    f"{STRAY}\n"
)


@pytest.fixture
def stand_in(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    client = TestClient(fake_llm_app)
    urls: list[str] = []

    def post(url: str, *, json: dict[str, Any], timeout: float) -> httpx.Response:
        urls.append(url)
        response: httpx.Response = client.post(url.removeprefix("http://fake-llm:8083"), json=json)
        return response

    monkeypatch.setattr(http_structured_llm_gateway.httpx, "post", post)
    return urls


def test_the_stand_in_proposes_its_fixed_type_and_places_labelled_lines(
    stand_in: list[str],
) -> None:
    gateway = HttpStructuredLLMGateway(BASE_URL)
    service = NoteTypeDeriveService(NoteImportService(llm_gateway=gateway), llm_gateway=gateway)

    derived = service.derive([SAMPLE])

    assert derived.spec.model_dump(exclude={"user_template"}) == DERIVED_PROPOSAL
    assert derived.guard == []
    (coverage,) = derived.coverage
    assert coverage.unplaced == [STRAY]
    assert len(stand_in) == 2


def test_the_route_dependency_uses_the_stand_in_only_when_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    configured = Settings(
        database_url="postgresql://x:x@localhost:5432/x",
        environment="development",
        note_generation_base_url=BASE_URL,
    )
    monkeypatch.setattr("app.routes.note_type_derive.get_settings", lambda: configured)
    assert isinstance(get_note_type_derive_service()._llm_gateway, HttpStructuredLLMGateway)

    unconfigured = Settings(
        database_url="postgresql://x:x@localhost:5432/x", environment="development"
    )
    monkeypatch.setattr("app.routes.note_type_derive.get_settings", lambda: unconfigured)
    assert not isinstance(get_note_type_derive_service()._llm_gateway, HttpStructuredLLMGateway)
