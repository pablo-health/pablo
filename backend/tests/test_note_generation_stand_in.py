# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""The end-to-end stack's stand-in for note drafting.

``note_generation_base_url`` sends standalone-note and preview drafts to an
HTTP service (``scripts/fake_llm.py``) instead of a model. These run the real
generation service against that service's app, so a draft comes back exactly
as the backend would validate a model's.
"""

from __future__ import annotations

import json
import time
from datetime import UTC, date, datetime
from typing import TYPE_CHECKING, Any

import pytest
from app.chart_proposals.drafting import propose_chart_updates
from app.models import Patient, Transcript
from app.notes import NoteTypeRegistry, register_builtin_note_types
from app.notes.chart_context import (
    ChartContext,
    ChartHistoryField,
    ChartMedication,
    ChartProblem,
)
from app.notes.practice_types import PracticeNoteTypeSpec, to_definition
from app.notes.spec_templates import TEMPLATES_DIR
from app.routes.notes import get_note_generation_service
from app.services import dictation_transcription, http_structured_llm_gateway
from app.services.ai_features import AIFeature
from app.services.dictation_transcription import HttpDictationTranscriber
from app.services.hedged_structured_llm_gateway import generation_gateway
from app.services.http_structured_llm_gateway import HttpStructuredLLMGateway
from app.services.note_generation_service import RegistryNoteGenerationService
from app.services.note_redraft import DICTATED_HEADING
from app.settings import Settings, get_settings
from fastapi.testclient import TestClient

from scripts.fake_llm import (
    DICTATION_TEXT,
    FALLBACK_MODEL,
    PRIMARY_DOWN,
    REFUSES_DRAFT,
    _current_medications,
    _stated_updates,
)
from scripts.fake_llm import app as fake_llm_app

from .test_practice_note_types import COACH_SPEC

if TYPE_CHECKING:
    import httpx
    from app.notes.registry import NoteTypeDefinition

BASE_URL = "http://fake-llm:8083/notes"
NOW = datetime(2026, 10, 5, tzinfo=UTC)
PATIENT = Patient(id="p", first_name="", last_name="", created_at=NOW, updated_at=NOW)
TRANSCRIPT = Transcript(format="txt", content="[00:01] Therapist: Hello\n[00:03] Client: Hi")


@pytest.fixture
def stand_in(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Route the gateway's POSTs to the stand-in's app; return the URLs hit."""
    client = TestClient(fake_llm_app)
    urls: list[str] = []

    def post(url: str, *, json: dict[str, Any], timeout: float) -> httpx.Response:
        urls.append(url)
        response: httpx.Response = client.post(url.removeprefix("http://fake-llm:8083"), json=json)
        return response

    monkeypatch.setattr(http_structured_llm_gateway.httpx, "post", post)
    return urls


def _service() -> RegistryNoteGenerationService:
    registry = NoteTypeRegistry()
    register_builtin_note_types(registry)
    return RegistryNoteGenerationService(
        registry=registry, llm_gateway=HttpStructuredLLMGateway(BASE_URL)
    )


def test_a_practice_type_gets_a_draft_in_its_own_shape(stand_in: list[str]) -> None:
    definition = to_definition("custom.coach", 1, PracticeNoteTypeSpec.model_validate(COACH_SPEC))

    generated = _service().generate_note(
        definition.key,
        TRANSCRIPT,
        PATIENT,
        NOW,
        inputs={"segment": "Network"},
        definition=definition,
    )

    assert generated.content == {
        "fix": {"one_thing": "Stand-in draft for fix.one_thing."},
        "log_row": {"channels": ["Stand-in draft for log_row.channels."]},
    }
    assert stand_in == [f"{BASE_URL}/v1/structured"]


def test_a_field_named_after_an_input_is_drafted_as_its_value(stand_in: list[str]) -> None:
    """So a spec can see which value reached the prompt."""
    spec = PracticeNoteTypeSpec.model_validate(
        {
            "label": "Visit",
            "sections": [
                {
                    "key": "billing",
                    "label": "Billing",
                    "fields": [
                        {"key": "visit_code", "label": "Visit code"},
                        {"key": "summary", "label": "Summary"},
                    ],
                }
            ],
            "inputs": [
                {"key": "visit_code", "label": "Visit code"},
                {"key": "program", "label": "Program"},
            ],
        }
    )
    definition = to_definition("custom.visit", 1, spec)

    generated = _service().generate_note(
        definition.key,
        TRANSCRIPT,
        PATIENT,
        NOW,
        inputs={"visit_code": "99214"},
        definition=definition,
    )

    assert generated.content == {
        "billing": {"visit_code": "99214", "summary": "Stand-in draft for billing.summary."}
    }
    assert len(stand_in) == 1


def test_a_diagnoses_field_gets_one_coded_diagnosis(stand_in: list[str]) -> None:
    spec = PracticeNoteTypeSpec.model_validate(
        {
            "label": "Evaluation",
            "sections": [
                {
                    "key": "assessment",
                    "label": "Assessment",
                    "fields": [{"key": "diagnoses", "label": "Diagnoses", "kind": "diagnoses"}],
                }
            ],
        }
    )
    definition = to_definition("custom.eval", 1, spec)

    generated = _service().generate_note(
        definition.key, TRANSCRIPT, PATIENT, NOW, definition=definition
    )

    assert generated.content == {
        "assessment": {
            "diagnoses": [
                {
                    "label": "Stand-in diagnosis for assessment.diagnoses",
                    "code": "F00.0",
                    "status": None,
                }
            ]
        }
    }


def test_what_was_dictated_lands_in_the_field_it_names(stand_in: list[str]) -> None:
    """A redraft with a dictation visibly gains it."""
    transcript = Transcript(
        format="txt",
        content=f"{TRANSCRIPT.content}\n\n{DICTATED_HEADING}\n\n{DICTATION_TEXT}",
    )

    generated = _service().generate_note("soap", transcript, PATIENT, NOW)

    assert generated.soap_note is not None
    assert generated.soap_note.plan.next_session.text == "Two weeks from today, same time."
    assert generated.soap_note.subjective.chief_complaint.text.startswith("Stand-in draft")


def test_every_dictated_clip_is_heard_as_the_same_words(monkeypatch: pytest.MonkeyPatch) -> None:
    client = TestClient(fake_llm_app)

    def post(url: str, *, content: bytes, headers: dict[str, str], timeout: float) -> Any:
        return client.post(
            url.removeprefix("http://fake-llm:8083"), content=content, headers=headers
        )

    monkeypatch.setattr(dictation_transcription.httpx, "post", post)
    transcriber = HttpDictationTranscriber("http://fake-llm:8083/transcription")

    assert transcriber.transcribe(b"\x1a\x45\xdf\xa3", "audio/webm") == DICTATION_TEXT


def test_a_soap_draft_survives_its_second_call(stand_in: list[str]) -> None:
    """SOAP asks again for sentence-to-transcript links; the stand-in answers none."""
    generated = _service().generate_note("soap", TRANSCRIPT, PATIENT, NOW)

    assert generated.soap_note is not None
    assert generated.soap_note.subjective.chief_complaint.text.startswith("Stand-in draft")
    assert len(stand_in) == 2


def test_a_refused_transcript_fails_its_draft_without_a_retry(stand_in: list[str]) -> None:
    """The stand-in's refusal is a failure the worker records at once, not one it retries."""
    transcript = Transcript(format="txt", content=f"[00:01] Therapist: {REFUSES_DRAFT}")

    with pytest.raises(ValueError, match="Note generation failed"):
        _service().generate_note("soap", transcript, PATIENT, NOW)

    assert len(stand_in) == 1


def test_the_route_dependency_uses_the_stand_in_only_when_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    configured = Settings(
        database_url="postgresql://x:x@localhost:5432/x",
        environment="development",
        note_generation_base_url=BASE_URL,
    )
    monkeypatch.setattr("app.routes.notes.get_settings", lambda: configured)
    service = get_note_generation_service()
    assert isinstance(service, RegistryNoteGenerationService)
    assert isinstance(service._llm_gateway, HttpStructuredLLMGateway)

    unconfigured = Settings(
        database_url="postgresql://x:x@localhost:5432/x", environment="development"
    )
    monkeypatch.setattr("app.routes.notes.get_settings", lambda: unconfigured)
    service = get_note_generation_service()
    assert isinstance(service, RegistryNoteGenerationService)
    assert not isinstance(service._llm_gateway, HttpStructuredLLMGateway)


def test_with_the_first_model_down_the_fallback_drafts(
    stand_in: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """As the stack runs it: the stand-in answers only the named fallback."""
    monkeypatch.setenv("AI_FALLBACKS", json.dumps({"note_generation": FALLBACK_MODEL}))
    get_settings.cache_clear()
    try:
        service = RegistryNoteGenerationService(
            registry=_service().registry,
            llm_gateway=generation_gateway(
                AIFeature.NOTE_GENERATION, HttpStructuredLLMGateway(BASE_URL)
            ),
        )
        transcript = Transcript(format="txt", content=f"[00:01] Client: {PRIMARY_DOWN}")
        generated = service.generate_note("soap", transcript, PATIENT, NOW)
    finally:
        get_settings.cache_clear()

    assert generated.soap_note is not None
    assert generated.soap_note.subjective.chief_complaint.text.startswith("Stand-in draft")
    # The draft and its sentence links: each the first model's 503, then the fallback.
    assert len(stand_in) == 4


def test_a_draft_echoes_the_chart_it_was_written_against(stand_in: list[str]) -> None:
    """The stand-in reads the chart block the backend really renders, not a copy of it."""
    spec = PracticeNoteTypeSpec.model_validate(
        {
            "label": "Follow-up",
            "sections": [
                {
                    "key": "assessment",
                    "label": "Assessment",
                    "fields": [
                        {"key": "diagnosis_summary", "label": "Diagnosis summary"},
                        {"key": "allergies", "label": "Allergies"},
                    ],
                }
            ],
        }
    )
    definition = to_definition("custom.follow_up", 1, spec)
    chart = ChartContext(
        problems=(
            ChartProblem("Generalized anxiety disorder", "F41.1", "active"),
            ChartProblem("Insomnia", None, "active"),
        ),
        allergy_status="nkda",
    )

    generated = _service().generate_note(
        definition.key, TRANSCRIPT, PATIENT, NOW, definition=definition, chart=chart
    )

    assert generated.content == {
        "assessment": {
            "diagnosis_summary": (
                "Stand-in draft for assessment.diagnosis_summary. Problem list: "
                "F41.1 Generalized anxiety disorder; Insomnia (no code recorded)."
            ),
            "allergies": (
                "Stand-in draft for assessment.allergies. "
                "Allergies: No known drug allergies (NKDA)."
            ),
        }
    }


Q_NEW = '(stated this visit: "...")'

FOLLOW_UP_TEMPLATE = TEMPLATES_DIR / "psychiatric_follow_up.json"


def _follow_up() -> NoteTypeDefinition:
    spec = PracticeNoteTypeSpec.model_validate(json.loads(FOLLOW_UP_TEMPLATE.read_text())["spec"])
    return to_definition("custom.psychiatric_follow_up", 1, spec)


def test_a_follow_up_states_the_charts_medication_list_not_the_visits_changes(
    stand_in: list[str],
) -> None:
    """The current list is the chart's, verbatim; a medication started today is not on it."""
    definition = _follow_up()
    chart = ChartContext(
        medications=(
            ChartMedication("Sertraline", "100 mg", "every morning", "psychiatric"),
            ChartMedication("Lisinopril", "10 mg", "daily", "other"),
        ),
    )
    transcript = Transcript(
        format="txt",
        content=(
            "[00:01] Clinician: Let's start bupropion XL 150 mg every morning.\n"
            "[00:05] Client: Okay."
        ),
    )

    generated = _service().generate_note(
        definition.key,
        transcript,
        PATIENT,
        NOW,
        inputs={"place_of_service": "In office"},
        definition=definition,
        chart=chart,
    )

    current = generated.content["medications"]["current_medications"]
    assert current == [
        "Psychiatric:",
        "Sertraline 100 mg, every morning",
        "Other:",
        "Lisinopril 10 mg, daily",
    ]
    assert not any("bupropion" in line.lower() for line in current)


def test_a_follow_up_with_no_medications_on_the_chart_says_none_recorded(
    stand_in: list[str],
) -> None:
    definition = _follow_up()

    generated = _service().generate_note(
        definition.key,
        TRANSCRIPT,
        PATIENT,
        NOW,
        inputs={"place_of_service": "In office"},
        definition=definition,
        chart=ChartContext(),
    )

    assert generated.content["medications"]["current_medications"] == ["None recorded"]


@pytest.mark.parametrize("template", ["psychiatric_follow_up", "psychiatric_evaluation"])
def test_the_prescriber_templates_take_current_medications_from_the_chart(template: str) -> None:
    spec = json.loads(FOLLOW_UP_TEMPLATE.with_name(f"{template}.json").read_text())["spec"]
    hints = {f["key"]: f.get("ai_hint") for section in spec["sections"] for f in section["fields"]}
    assert hints["current_medications"].startswith(
        "From the chart, exactly as given, or 'None recorded'. Then each medication the client "
        "reports currently taking that the chart lacks"
    )
    assert Q_NEW in hints["current_medications"]
    assert Q_NEW in hints["allergies"]
    assert "Never write NKDA unless the chart says it." in hints["allergies"]


def test_a_follow_up_writes_the_charts_history_word_for_word(stand_in: list[str]) -> None:
    """Each history field is the chart's text for its key; a substance field adds its screen."""
    definition = _follow_up()
    chart = ChartContext(
        history=(
            ChartHistoryField(
                "prior_diagnoses", "ADHD, combined type, diagnosed 2019.", date(2026, 7, 14)
            ),
            ChartHistoryField(
                "living_situation",
                "Separated in August; lives alone.\nSees the children on weekends.",
                date(2026, 9, 2),
            ),
            ChartHistoryField("alcohol", "Two glasses of wine on weekends.", date(2026, 7, 14)),
        ),
    )

    generated = _service().generate_note(
        definition.key,
        TRANSCRIPT,
        PATIENT,
        NOW,
        inputs={"place_of_service": "In office"},
        definition=definition,
        chart=chart,
    )

    content = generated.content
    assert content["psychiatric_history"]["prior_diagnoses"] == (
        "ADHD, combined type, diagnosed 2019."
    )
    assert content["social_history"]["living_situation"] == (
        "Separated in August; lives alone.\nSees the children on weekends."
    )
    assert content["substance_use"]["alcohol"] == (
        "Two glasses of wine on weekends. (not asked this visit)"
    )
    # A field the chart has nothing for reads as a model writes it, not as a draft of the visit.
    assert content["social_history"]["relationships"] == "Not recorded"
    assert content["family_history"]["family_medical"] == "Not recorded"


def _draft_current_medications(chart: ChartContext, transcript: str) -> list[str]:
    definition = _follow_up()
    generated = _service().generate_note(
        definition.key,
        Transcript(format="txt", content=transcript),
        PATIENT,
        NOW,
        inputs={"place_of_service": "In office"},
        definition=definition,
        chart=chart,
    )
    current: list[str] = generated.content["medications"]["current_medications"]
    return current


def test_medications_the_client_reports_are_added_after_an_empty_chart(
    stand_in: list[str],
) -> None:
    """Chart empty, client lists two: the draft never says none while they take two."""
    current = _draft_current_medications(
        ChartContext(),
        "[00:01] Therapist: What are you taking right now?\n"
        "[00:04] Client: I'm taking sertraline 50 mg and trazodone 50 mg at night.",
    )
    assert current == [
        "None recorded",
        '(stated this visit: "sertraline 50 mg")',
        '(stated this visit: "trazodone 50 mg at night")',
    ]


def test_a_reported_medication_already_on_the_chart_is_listed_once_unmarked(
    stand_in: list[str],
) -> None:
    current = _draft_current_medications(
        ChartContext(medications=(ChartMedication("Sertraline", "100 mg", "every morning"),)),
        "[00:04] Client: I'm taking sertraline 100 mg.",
    )
    assert current == ["Sertraline 100 mg, every morning"]


def test_a_history_field_keeps_the_charts_text_and_adds_what_the_visit_changed(
    stand_in: list[str],
) -> None:
    definition = _follow_up()
    chart = ChartContext(
        history=(
            ChartHistoryField("work_school", "Employed at a logistics firm.", date(2026, 7, 14)),
            ChartHistoryField("supports", "Sister nearby.", date(2026, 7, 14)),
        )
    )
    generated = _service().generate_note(
        definition.key,
        Transcript(
            format="txt",
            content="[00:04] Client: Update on work_school: laid off last week.",
        ),
        PATIENT,
        NOW,
        inputs={"place_of_service": "In office"},
        definition=definition,
        chart=chart,
    )
    social = generated.content["social_history"]
    assert social["work_school"] == (
        'Employed at a logistics firm. (stated this visit: "laid off last week.")'
    )
    assert social["supports"] == "Sister nearby."


@pytest.mark.parametrize(
    "line",
    [
        "Client: I'm taking " + "a" * 10_000,
        "Client: I'm taking a" * 1_000,
        "Client: I'm taking " + " " * 10_000 + "x",
        "Client: I'm taking " + " and" * 2_500,
        "Client: Update on " + "x" * 10_000,
    ],
)
def test_the_stand_in_reads_a_pathological_line_in_bounded_time(line: str) -> None:
    """What a client line names is read with string operations, never a backtracking regex."""
    started = time.perf_counter()
    _current_medications([], line)
    _stated_updates(line)
    assert time.perf_counter() - started < 0.5


def test_the_stand_in_proposes_what_a_line_says_for_the_chart(stand_in: list[str]) -> None:
    """A client line ``Update on <key>: <text>`` is a proposal citing that line."""
    transcript = Transcript(
        format="txt",
        content="[00:01] Therapist: Anything new?\n"
        "[00:04] Client: Update on work_school: Laid off in March.\n"
        "[00:09] Client: Update on allergies: Penicillin - hives.",
    )

    proposals = propose_chart_updates(
        _service().chart_proposal_completion(), ChartContext(), transcript
    ).proposals

    assert [(p.field_key, p.item_key, p.proposed_text) for p in proposals] == [
        ("work_school", "", "Laid off in March."),
        ("allergies", "Penicillin", "hives."),
    ]
    assert [[e.segment_id for e in p.evidence] for p in proposals] == [[1], [2]]


@pytest.mark.parametrize(
    ("transcript", "alcohol", "cannabis"),
    [
        (
            "[00:04] Client: No change in alcohol.",
            "Two glasses of wine a week. (asked this visit: no change)",
            "Not recorded (not asked this visit)",
        ),
        (
            "[00:04] Client: Update on alcohol: stopped drinking in September.\n"
            "[00:06] Client: Update on cannabis: a few times a month.",
            'Two glasses of wine a week. (stated this visit: "stopped drinking in September.")',
            'Not recorded (stated this visit: "a few times a month.")',
        ),
    ],
)
def test_a_substance_field_is_the_baseline_then_the_visits_screen(
    stand_in: list[str], transcript: str, alcohol: str, cannabis: str
) -> None:
    definition = _follow_up()
    chart = ChartContext(
        history=(ChartHistoryField("alcohol", "Two glasses of wine a week.", date(2026, 7, 14)),)
    )
    generated = _service().generate_note(
        definition.key,
        Transcript(format="txt", content=transcript),
        PATIENT,
        NOW,
        inputs={"place_of_service": "In office"},
        definition=definition,
        chart=chart,
    )
    substance = generated.content["substance_use"]
    assert (substance["alcohol"], substance["cannabis"]) == (alcohol, cannabis)


_INTERLEAVED = "\n".join(
    [
        "[00:00:05] Therapist: Hi, good to see you today.",
        "[00:01:00] Therapist: Now let's get into the session work you wanted.",
        "[00:01:30] Client: I keep replaying the argument with my sister.",
        "[00:15:00] Therapist: Quick check: any side effects since the dose change?",
        "[00:16:00] Client: No, sleep is better than it was.",
        "[00:20:00] Therapist: Back to the argument. What did you tell yourself?",
        "[00:35:00] Therapist: Any thoughts of hurting yourself?",
        "[00:35:30] Client: No, none at all, not even close.",
        "[00:40:10] Client: Thank you, see you next month then.",
        "[00:41:00] Therapist: Addendum. Psychotherapy 30 minutes.",
    ]
)


def test_the_stand_in_labels_the_turns_and_hears_the_dictated_minutes(
    stand_in: list[str],
) -> None:
    spec = {
        "label": "Follow-up",
        "sections": [
            {
                "key": "psychotherapy",
                "label": "Psychotherapy",
                "fields": [{"key": "psychotherapy_time", "label": "Psychotherapy time"}],
            }
        ],
    }
    definition = to_definition("custom.e2e", 1, PracticeNoteTypeSpec.model_validate(spec))

    generated = _service().generate_note(
        definition.key,
        Transcript(format="txt", content=_INTERLEAVED),
        PATIENT,
        NOW,
        definition=definition,
        client_present_end_seconds=2414.0,
    )

    proposal = generated.psychotherapy_proposal
    assert proposal is not None
    assert [run["label"] for run in proposal["labels"]] == [
        "admin",
        "therapy",
        "therapy",
        "medication_management",
        "medication_management",
        "therapy",
        "screening_risk",
        "screening_risk",
        "admin",
    ]
    assert proposal["cue_seconds"] == 60.0
    assert proposal["dictated"]["minutes"] == 30
    assert generated.content["psychotherapy"]["psychotherapy_time"] == "30 minutes"
