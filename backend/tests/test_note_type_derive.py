# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Deriving a note type from sample notes or a description."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

import pytest
from app.auth.service import get_current_user, require_baa_acceptance
from app.main import app
from app.models.audit import AuditAction, ResourceType
from app.notes import NoteTypeRegistry, register_builtin_note_types
from app.notes.practice_types import PracticeNoteTypeSpec, RepositoryPracticeNoteTypeSource
from app.notes.references import (
    RequiredElement,
    clear_registered_note_type_references,
    register_note_type_reference,
)
from app.repositories import (
    InMemoryPracticeNoteTypeRepository,
    get_practice_note_type_repository,
)
from app.repositories.audit import InMemoryAuditRepository
from app.routes.note_type_derive import get_note_type_derive_service
from app.routes.note_types import get_registry
from app.services.audit_service import AuditService, get_audit_service
from app.services.note_import_service import NoteImportService
from app.services.note_type_derive_checks import SampleText, copied_paths
from app.services.note_type_derive_service import (
    NoteTypeDeriveService,
    derive_response_schema,
    normalize_proposal,
)
from app.services.structured_llm_gateway import (
    FakeStructuredLLMGateway,
    StructuredCompletion,
)
from fastapi.testclient import TestClient

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

    from app.models import User

# Synthetic: written for this test, about no one.
SAMPLE = """\
Interval history: Pat Anonymous reports sleeping seven hours since the dose change in March.
Current medications: sertraline 50 mg each morning
Plan: Return in four weeks for a medication check.
"""

STRAY = "Pat brought a watercolor of the lighthouse at Faketown harbor to share today."

PROPOSAL: dict[str, Any] = {
    "label": "Medication follow-up",
    "description": "A short follow-up after a medication change.",
    "system_prompt": "Write brief clinical prose in the third person, past tense.",
    "sections": [
        {
            "key": "interval",
            "label": "Interval history",
            "fields": [
                {
                    "key": "interval_history",
                    "label": "Interval history",
                    "kind": "text",
                    "ai_hint": "What changed since the last visit, in the client's terms.",
                },
                {
                    "key": "current_medications",
                    "label": "Current medications",
                    "kind": "list",
                    "ai_hint": "Each medication and dose as reviewed.",
                },
            ],
        },
        {
            "key": "plan",
            "label": "Plan",
            "fields": [
                {
                    "key": "plan",
                    "label": "Plan",
                    "kind": "text",
                    "ai_hint": "Next steps and when the client returns.",
                }
            ],
        },
    ],
    "inputs": [],
}

EXTRACTED = {
    "interval": {
        "interval_history": (
            "Pat Anonymous reports sleeping seven hours since the dose change in March."
        ),
        "current_medications": ["sertraline 50 mg each morning"],
    },
    "plan": {"plan": "Return in four weeks for a medication check."},
}


def _reply(data: dict[str, Any]) -> StructuredCompletion:
    return StructuredCompletion(data=data)


def _with_hint(hint: str) -> dict[str, Any]:
    proposal = {**PROPOSAL, "sections": [dict(s) for s in PROPOSAL["sections"]]}
    fields = [dict(f) for f in proposal["sections"][0]["fields"]]
    fields[0]["ai_hint"] = hint
    proposal["sections"][0]["fields"] = fields
    return proposal


@pytest.fixture
def gateway() -> FakeStructuredLLMGateway:
    return FakeStructuredLLMGateway()


@pytest.fixture
def repo() -> InMemoryPracticeNoteTypeRepository:
    return InMemoryPracticeNoteTypeRepository()


@pytest.fixture
def audit_repo() -> InMemoryAuditRepository:
    return InMemoryAuditRepository()


@pytest.fixture(autouse=True)
def _no_registered_references() -> Iterator[None]:
    clear_registered_note_type_references()
    yield
    clear_registered_note_type_references()


@pytest.fixture
def client(
    gateway: FakeStructuredLLMGateway,
    repo: InMemoryPracticeNoteTypeRepository,
    audit_repo: InMemoryAuditRepository,
    mock_user: User,
) -> Iterator[TestClient]:
    registry = NoteTypeRegistry()
    register_builtin_note_types(registry)
    registry.set_practice_source(RepositoryPracticeNoteTypeSource(lambda: repo))
    overrides: dict[Callable[..., Any], Callable[..., Any]] = {
        get_current_user: lambda: mock_user,
        require_baa_acceptance: lambda: mock_user,
        get_practice_note_type_repository: lambda: repo,
        get_registry: lambda: registry,
        get_audit_service: lambda: AuditService(audit_repo),
        get_note_type_derive_service: lambda: NoteTypeDeriveService(
            NoteImportService(llm_gateway=gateway, model="m"), llm_gateway=gateway, model="m"
        ),
    }
    app.dependency_overrides.update(overrides)
    try:
        yield TestClient(app)
    finally:
        for dependency in overrides:
            app.dependency_overrides.pop(dependency, None)


def _derive(client: TestClient, **form: Any) -> Any:
    files = form.pop("files", None)
    return client.post("/api/note-types/derive", data=form, files=files)


# ---------------------------------------------------------------------------
# The proposal
# ---------------------------------------------------------------------------


def test_a_description_alone_yields_a_valid_spec(
    client: TestClient, gateway: FakeStructuredLLMGateway
) -> None:
    gateway.responses = [_reply(PROPOSAL)]

    response = _derive(client, description="Interval history, then medications, then the plan.")

    assert response.status_code == 200, response.text
    body = response.json()
    PracticeNoteTypeSpec.model_validate(body["spec"])
    assert [s["key"] for s in body["spec"]["sections"]] == ["interval", "plan"]
    assert body["coverage"] == []
    assert body["guard"] == []
    (call,) = gateway.calls
    assert "Interval history, then medications" in call["user_prompt"]
    assert call["response_schema"]["title"] == "PracticeNoteTypeSpec"


def test_a_sample_yields_its_sections_in_order_and_full_coverage(
    client: TestClient, gateway: FakeStructuredLLMGateway
) -> None:
    gateway.responses = [_reply(PROPOSAL), _reply(EXTRACTED)]

    response = _derive(client, samples=[SAMPLE])

    assert response.status_code == 200, response.text
    body = response.json()
    assert [s["label"] for s in body["spec"]["sections"]] == ["Interval history", "Plan"]
    assert body["coverage"] == [{"sample": 0, "passages": 3, "unplaced": [], "checked": True}]
    extract_call = gateway.calls[1]
    assert extract_call["thinking_budget"] == 0
    assert SAMPLE.strip() in extract_call["user_prompt"]
    assert set(extract_call["response_schema"]["properties"]) == {"interval", "plan"}


def test_a_paragraph_outside_the_proposal_is_reported_unplaced(
    client: TestClient, gateway: FakeStructuredLLMGateway
) -> None:
    gateway.responses = [_reply(PROPOSAL), _reply(EXTRACTED)]

    response = _derive(client, samples=[f"{SAMPLE}\n{STRAY}\n"])

    assert response.status_code == 200, response.text
    (coverage,) = response.json()["coverage"]
    assert coverage["unplaced"] == [STRAY]
    assert coverage["passages"] == 4


def test_an_invalid_proposal_is_repaired_once(
    client: TestClient, gateway: FakeStructuredLLMGateway
) -> None:
    gateway.responses = [_reply({**PROPOSAL, "sections": []}), _reply(PROPOSAL)]

    response = _derive(client, description="Two sections.")

    assert response.status_code == 200, response.text
    assert len(gateway.calls) == 2
    assert "rejected" in gateway.calls[1]["user_prompt"]
    assert "sections" in gateway.calls[1]["user_prompt"]


def test_a_proposal_that_fails_its_repair_is_refused(
    client: TestClient, gateway: FakeStructuredLLMGateway
) -> None:
    gateway.responses = [_reply({"sections": []}), _reply({"sections": []})]

    response = _derive(client, description="Two sections.")

    assert response.status_code == 422
    assert len(gateway.calls) == 2


def test_a_model_outage_is_a_503(client: TestClient, gateway: FakeStructuredLLMGateway) -> None:
    gateway.responses = [RuntimeError("unavailable")]

    assert _derive(client, description="Two sections.").status_code == 503


def test_mechanical_problems_are_fixed_before_validation() -> None:
    raw = {
        "label": "Visit",
        "user_template": "{transcript}",
        "sections": [
            {
                "key": "Interval History",
                "label": "Interval",
                "fields": [
                    {"key": "Notes", "label": "Notes", "kind": "prose"},
                    {"key": "notes", "label": "More notes"},
                ],
            }
        ],
        "inputs": [{"key": "where", "label": "Where", "kind": "choice", "options": ["Office"]}],
    }

    spec = PracticeNoteTypeSpec.model_validate(normalize_proposal(raw))

    assert spec.user_template is None
    assert spec.sections[0].key == "interval_history"
    assert [f.key for f in spec.sections[0].fields] == ["notes", "notes_2"]
    assert spec.sections[0].fields[0].kind == "text"
    assert spec.inputs[0].kind == "text"
    assert spec.inputs[0].options == []


def test_the_response_schema_is_the_spec_schema_inlined() -> None:
    schema = derive_response_schema()

    assert "$ref" not in repr(schema)
    assert "user_template" not in schema["properties"]
    section = schema["properties"]["sections"]["items"]
    field = section["properties"]["fields"]["items"]
    assert field["properties"]["kind"]["enum"] == ["text", "list", "diagnoses"]


# ---------------------------------------------------------------------------
# Copied sample text
# ---------------------------------------------------------------------------

SEEDED = "Note things like Pat Anonymous reports sleeping seven hours since the dose change."


def test_a_hint_seeded_with_sample_text_is_rewritten(
    client: TestClient, gateway: FakeStructuredLLMGateway
) -> None:
    gateway.responses = [
        _reply(_with_hint(SEEDED)),
        _reply({"item_0": "Changes the client describes since the previous visit."}),
        _reply(EXTRACTED),
    ]

    body = _derive(client, samples=[SAMPLE]).json()

    hint = body["spec"]["sections"][0]["fields"][0]["ai_hint"]
    assert hint == "Changes the client describes since the previous visit."
    assert body["guard"] == [
        {"path": "sections[0].fields[0].ai_hint", "outcome": "rewritten"},
    ]
    assert SEEDED in gateway.calls[1]["user_prompt"]


def test_a_hint_still_copied_after_its_rewrite_is_neutralized(
    client: TestClient, gateway: FakeStructuredLLMGateway
) -> None:
    gateway.responses = [
        _reply(_with_hint(SEEDED)),
        _reply({"item_0": "e.g. reports sleeping seven hours since the dose change in March"}),
        _reply(EXTRACTED),
    ]

    body = _derive(client, samples=[SAMPLE]).json()

    hint = body["spec"]["sections"][0]["fields"][0]["ai_hint"]
    assert hint == "What the visit covered for Interval history."
    assert body["guard"] == [
        {"path": "sections[0].fields[0].ai_hint", "outcome": "neutralized"},
    ]
    spec = PracticeNoteTypeSpec.model_validate(body["spec"])
    assert copied_paths(spec, SampleText([SAMPLE])) == []


def test_copied_labels_keys_and_prompt_are_neutralized_when_the_rewrite_fails(
    client: TestClient, gateway: FakeStructuredLLMGateway
) -> None:
    proposal = {
        **PROPOSAL,
        "system_prompt": "Mention that Pat Anonymous reports sleeping seven hours.",
        "sections": [
            {
                "key": "pat_anonymous_reports_sleeping_seven_hours",
                "label": "Pat Anonymous reports sleeping seven hours",
                "fields": PROPOSAL["sections"][0]["fields"],
            },
            PROPOSAL["sections"][1],
        ],
    }
    gateway.responses = [_reply(proposal), RuntimeError("down"), _reply(EXTRACTED)]

    body = _derive(client, samples=[SAMPLE]).json()

    assert body["spec"]["system_prompt"] == ""
    assert body["spec"]["sections"][0]["key"] == "section_1"
    assert body["spec"]["sections"][0]["label"] == "Section 1"
    assert {g["outcome"] for g in body["guard"]} == {"neutralized"}
    assert "Pat" not in repr(body["spec"])


def test_a_sample_heading_used_as_a_label_is_structure_not_a_copy() -> None:
    index = SampleText(["CHIEF COMPLAINT AND REASON FOR VISIT\nClient said little."])

    assert not index.copied("Chief complaint and reason for visit", heading_allowed=True)
    assert index.copied("Chief complaint and reason for visit")


def test_a_name_copied_on_its_own_is_a_copy() -> None:
    """A name shares no run of words with its sample; it is caught by itself."""
    index = SampleText([SAMPLE, STRAY])

    assert index.copied("Visits like the ones with Anonymous after a change.")
    assert index.copied("Mention the trip to Faketown.")
    # Capitalized only at the start of a sentence, or a heading: not a name.
    assert not index.copied("Return when the plan says to.")
    assert not index.copied("Current medications and doses, as reviewed.")


def test_a_title_case_heading_word_is_not_a_name() -> None:
    index = SampleText(["Chief Complaint: low mood for two weeks.\nThe chief complaint is new."])

    assert not index.copied("The client's complaint in their own words.")


# ---------------------------------------------------------------------------
# Nothing kept, everything audited
# ---------------------------------------------------------------------------


def test_samples_are_not_stored_logged_or_audited(
    client: TestClient,
    gateway: FakeStructuredLLMGateway,
    repo: InMemoryPracticeNoteTypeRepository,
    audit_repo: InMemoryAuditRepository,
    caplog: pytest.LogCaptureFixture,
) -> None:
    gateway.responses = [_reply(PROPOSAL), _reply(EXTRACTED), _reply(EXTRACTED)]
    caplog.set_level(logging.DEBUG)

    response = _derive(
        client,
        samples=[f"{SAMPLE}\n{STRAY}"],
        files={"files": ("note.txt", SAMPLE.encode(), "text/plain")},
    )

    assert response.status_code == 200, response.text
    assert repo.list_latest() == []
    (entry,) = audit_repo._entries
    assert entry.action == AuditAction.NOTE_TYPE_DERIVED.value
    assert entry.resource_type == ResourceType.NOTE_TYPE.value
    assert entry.changes == {
        "pasted_samples": 1,
        "file_samples": 1,
        "description": False,
        "reference": None,
    }
    for text in ("Pat Anonymous", "sertraline", "watercolor"):
        assert text not in repr(entry)
        assert text not in caplog.text


@pytest.mark.parametrize(
    ("form", "status_code"),
    [
        ({}, 400),
        ({"samples": ["   "]}, 400),
        ({"samples": [SAMPLE] * 4}, 400),
        ({"samples": ["x" * 50_001]}, 400),
        ({"description": "x", "reference": "no-such-reference"}, 404),
    ],
)
def test_bad_requests_are_refused_before_any_model_call(
    client: TestClient, gateway: FakeStructuredLLMGateway, form: dict[str, Any], status_code: int
) -> None:
    assert _derive(client, **form).status_code == status_code
    assert gateway.calls == []


def test_an_unsupported_file_is_refused(
    client: TestClient, gateway: FakeStructuredLLMGateway
) -> None:
    response = _derive(client, files={"files": ("note.png", b"\x89PNG", "image/png")})

    assert response.status_code == 415
    assert gateway.calls == []


# ---------------------------------------------------------------------------
# References
# ---------------------------------------------------------------------------


def test_a_built_in_type_as_reference_lists_what_the_proposal_lacks(
    client: TestClient, gateway: FakeStructuredLLMGateway
) -> None:
    gateway.responses = [_reply(PROPOSAL)]

    body = _derive(client, description="Two sections.", reference="soap").json()

    assert body["reference"] == {"key": "soap", "label": "SOAP"}
    assert [s["label"] for s in body["suggestions"]] == ["Subjective", "Objective", "Assessment"]


def test_a_registered_reference_is_compared_and_listed(
    client: TestClient, gateway: FakeStructuredLLMGateway
) -> None:
    register_note_type_reference(
        "follow_up_checklist",
        "Follow-up checklist",
        [
            RequiredElement("Current medications", description="Each one, with dose."),
            RequiredElement("Time spent", terms=("minutes", "duration")),
        ],
    )
    gateway.responses = [_reply(PROPOSAL)]

    body = _derive(client, description="Two sections.", reference="follow_up_checklist").json()

    assert body["suggestions"] == [{"label": "Time spent", "description": ""}]
    listed = client.get("/api/note-types/derive/references").json()
    assert listed == {
        "references": [{"key": "follow_up_checklist", "label": "Follow-up checklist"}]
    }
