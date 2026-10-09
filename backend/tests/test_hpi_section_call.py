# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""The history of present illness call, with no model involved.

The composition is pure: the chief complaint's quotation of the client is kept
only when every line it cites contains its words, and a domain the visit never
touched reads as its hint says. The routing reads the definition. Through the
generation service a scripted gateway answers each call by the schema it sends,
so what each call was asked can be read back.
"""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import pytest
from app.models import Patient, Transcript
from app.notes.chart_context import ChartContext
from app.notes.note_type_patch import resolve_spec
from app.notes.practice_spec import NoteTypePatch
from app.notes.practice_types import PracticeNoteTypeSpec, to_definition
from app.notes.registry import NoteFieldDef, NoteTypeDefinition
from app.notes.section_calls import (
    HPI,
    RISK_MSE,
    SectionField,
    model_key,
    section_call_fields,
    without_fields,
)
from app.notes.spec_templates import TEMPLATES_DIR
from app.services import note_generation_service
from app.services.chart_field_extraction import SCHEMA_TITLE as EXTRACTION_TITLE
from app.services.hpi_section_call import (
    NOT_DISCUSSED,
    NOT_STATED,
    SCHEMA_TITLE,
    SYSTEM_PROMPT,
    build_prompt,
    compose,
    compose_chief_complaint,
    response_schema,
    uncovered_text,
)
from app.services.note_generation_service import (
    HPI_SECTION_FAILED_EVENT,
    RegistryNoteGenerationService,
)
from app.services.risk_section_call import SCHEMA_TITLE as RISK_TITLE
from app.services.structured_llm_gateway import StructuredCompletion, StructuredLLMGateway
from app.settings import Settings

NOW = datetime(2026, 10, 9, 15, tzinfo=UTC)
PATIENT = Patient(id="p", first_name="", last_name="", created_at=NOW, updated_at=NOW)

CURLY = chr(0x2019)
SEGMENTS = {
    0: "[00:01] Therapist: What brings you in today?",
    1: f"[00:04] Client: My worry{CURLY}s been through the roof since the layoff.",
    2: "[00:09] Therapist: How has your mood been?",
    3: "[00:12] Client: Low, honestly. I can't get into anything.",
}
FOLLOW_UP_ROUTED = {"subjective"}
EVALUATION_ROUTED = {"chief_complaint", "hpi", "psychiatric_ros"}


def _spec(template: str) -> PracticeNoteTypeSpec:
    raw = json.loads((TEMPLATES_DIR / f"{template}.json").read_text())["spec"]
    return PracticeNoteTypeSpec.model_validate(raw)


def _definition(template: str = "psychiatric_follow_up") -> NoteTypeDefinition:
    return to_definition(f"custom.{template}", 1, _spec(template))


def _fields(template: str = "psychiatric_follow_up") -> dict[str, SectionField]:
    return {f.path: f for f in section_call_fields(_definition(template), HPI)}


def _quote(words: str, ids: Any) -> dict[str, Any]:
    return {"words": words, "segment_ids": ids}


# ---------------------------------------------------------------------------
# The chief complaint: the client's words only when the line holds them
# ---------------------------------------------------------------------------

REASON = "Medication follow-up for worsening anxiety."


def test_a_chief_complaint_quotation_its_line_holds_is_kept_after_the_reason() -> None:
    text = compose_chief_complaint(
        REASON, [_quote("my worry's been through the roof", [1])], SEGMENTS, "client", NOT_STATED
    )
    # Copied from the line: its case and its apostrophe, not the reply's.
    assert text == f'{REASON} The client said: "My worry{CURLY}s been through the roof"'


def test_a_chief_complaint_quotation_its_line_does_not_hold_is_dropped_and_the_reason_stands() -> (
    None
):
    """A paraphrase passed off as the client's words never reaches the note."""
    text = compose_chief_complaint(
        REASON, [_quote("I've been so anxious lately", [1])], SEGMENTS, "client", NOT_STATED
    )
    assert text == REASON
    assert '"' not in text


@pytest.mark.parametrize("ids", [[], None, [9], [True], ["1"]])
def test_a_chief_complaint_quotation_citing_no_line_or_one_the_visit_lacks_is_dropped(
    ids: Any,
) -> None:
    text = compose_chief_complaint(
        REASON, [_quote("through the roof", ids)], SEGMENTS, "client", NOT_STATED
    )
    assert text == REASON


def test_a_chief_complaint_quotation_must_be_in_every_line_it_cites() -> None:
    """Citing the question beside the answer is not evidence that the client said it."""
    text = compose_chief_complaint(
        "", [_quote("through the roof", [0, 1])], SEGMENTS, "client", NOT_STATED
    )
    assert text == NOT_STATED


def test_a_chief_complaint_with_nothing_kept_reads_not_stated_and_gives_way_to_a_kept_quote() -> (
    None
):
    assert compose_chief_complaint("", [], SEGMENTS, "client", NOT_STATED) == NOT_STATED
    kept = compose_chief_complaint(
        "Not stated.", [_quote("Low, honestly", [3])], SEGMENTS, "client", NOT_STATED
    )
    assert kept == 'The client said: "Low, honestly"'


def test_the_chief_complaint_frames_a_quotation_with_the_charts_word_for_the_person() -> None:
    text = compose_chief_complaint("", [_quote("Low", [3])], SEGMENTS, "patient", NOT_STATED)
    assert text == 'The patient said: "Low"'


# ---------------------------------------------------------------------------
# A domain the visit never touched
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("reply", ["", "Not discussed.", "not discussed", "Not asked.", None])
def test_a_follow_up_domain_the_visit_never_touched_reads_not_discussed(reply: Any) -> None:
    fields = list(_fields().values())
    content = compose({"subjective": {"mania": reply}}, fields, SEGMENTS, "client")
    assert content["subjective"]["mania"] == NOT_DISCUSSED


def test_an_evaluation_review_item_the_visit_never_touched_reads_not_asked() -> None:
    """Each field reads the words its own hint gives, not one marker for every template."""
    fields = list(_fields("psychiatric_evaluation").values())
    content = compose({"psychiatric_ros": {"psychosis": "Not discussed."}}, fields, SEGMENTS, "c")
    assert content["psychiatric_ros"]["psychosis"] == "Not asked."
    assert content["psychiatric_ros"]["eating"] == "Not asked."
    # The history of present illness proper names no marker; the list stays a list.
    assert content["hpi"]["modifying_factors"] == NOT_STATED
    assert content["hpi"]["symptoms"] == []


def test_a_domain_the_visit_covered_is_the_reply_as_written() -> None:
    fields = list(_fields().values())
    written = "Reports low mood and loss of interest over two weeks; denies hopelessness."
    content = compose({"subjective": {"depression": written}}, fields, SEGMENTS, "client")
    assert content["subjective"]["depression"] == written
    # A field with no marker in its hint, left empty, reads "Not stated.".
    assert content["subjective"]["adherence"] == NOT_STATED


def test_a_field_whose_hint_says_to_leave_it_empty_stays_empty() -> None:
    f = SectionField(
        "subjective",
        NoteFieldDef("work_notes", "Work notes", "text", ai_hint="Leave empty if none."),
        quotes=False,
    )
    assert uncovered_text(f) == ""
    assert compose({}, [f], SEGMENTS, "client") == {"subjective": {"work_notes": ""}}


# ---------------------------------------------------------------------------
# Routing
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("template", "sections"),
    [("psychiatric_follow_up", FOLLOW_UP_ROUTED), ("psychiatric_evaluation", EVALUATION_ROUTED)],
)
def test_both_prescriber_templates_route_their_history_of_present_illness(
    template: str, sections: set[str]
) -> None:
    definition = _definition(template)
    routed = section_call_fields(definition, HPI)
    assert {f.section for f in routed} == sections
    for section in definition.sections:
        if section.key in sections:
            assert {f.field.key for f in routed if f.section == section.key} == set(
                section.field_keys()
            )
    # Only the chief complaint carries the client's words.
    assert [f.field.key for f in routed if f.quotes] == ["chief_complaint"]
    # Nothing routed to one call is routed to the other.
    assert not {f.path for f in routed} & {
        f.path for f in section_call_fields(definition, RISK_MSE)
    }


def test_the_follow_ups_history_is_each_symptom_domain_with_course_adherence_and_side_effects() -> (
    None
):
    assert list(_fields()) == [
        f"subjective.{k}"
        for k in (
            "chief_complaint",
            "depression",
            "anxiety",
            "insomnia_sleep",
            "inattention_hyperactivity",
            "mania",
            "appetite_eating",
            "onset_duration_course",
            "recent_stressors",
            "functioning",
            "adherence",
            "side_effects",
        )
    ]


def test_a_domain_a_practice_adds_to_a_based_types_history_is_drafted_by_the_history_call() -> None:
    patch = NoteTypePatch(
        add_fields=[
            {
                "section": "subjective",
                "field": {
                    "key": "irritability",
                    "label": "Irritability",
                    "ai_hint": 'Irritability as stated. "Not discussed." if it did not come up.',
                },
            },
            {"section": "plan", "field": {"key": "shared_decision", "label": "Shared decision"}},
        ],
        hide_fields=["subjective.mania"],
    )
    based = to_definition("custom.based", 1, resolve_spec(_spec("psychiatric_follow_up"), patch))
    routed = {f.path: f for f in section_call_fields(based, HPI)}
    assert "subjective.irritability" in routed
    assert "subjective.mania" not in routed
    assert "plan.shared_decision" not in routed
    assert uncovered_text(routed["subjective.irritability"]) == NOT_DISCUSSED
    main = without_fields(based, list(routed.values()))
    assert "subjective" not in main.section_keys()
    assert "shared_decision" in main.sections[main.section_keys().index("plan")].field_keys()


def test_the_history_call_has_its_own_model_key() -> None:
    assert model_key(HPI) == "note_generation.hpi"


# ---------------------------------------------------------------------------
# The prompt and the schema
# ---------------------------------------------------------------------------


def test_the_instruction_is_short_and_uses_the_charts_word_for_the_person() -> None:
    prompt = build_prompt(list(_fields().values()), "patient", "[S0] [00:01] Patient: Fine.")
    instruction = prompt.split("\n\nFields:", 1)[0]
    assert len(instruction.splitlines()) <= 12
    assert "the patient's last line" in instruction
    assert '"Not discussed."' in instruction
    assert "client" not in instruction.lower()
    assert "- subjective.mania (Mania): What changed since the last visit" in prompt
    assert "- subjective.chief_complaint (Chief complaint; text and quotes): " in prompt
    assert prompt.endswith("Transcript (each line numbered [Sn]):\n[S0] [00:01] Patient: Fine.")


def test_the_history_call_never_hears_of_a_dictated_addendum() -> None:
    """The templates' system prompt names "the clinician's dictated addendum", and the
    history echoed it ("per clinician", "dictated addendum"). This call has its own."""
    prompt = build_prompt(list(_fields().values()), "client", "")
    for text in (SYSTEM_PROMPT, prompt):
        assert "addendum" not in text.lower()
        assert "per clinician" not in text.lower()


def test_the_schema_asks_for_the_chief_complaints_quotes_apart_and_plain_text_elsewhere() -> None:
    schema = response_schema(list(_fields("psychiatric_evaluation").values()))
    assert schema["title"] == SCHEMA_TITLE
    assert set(schema["properties"]) == EVALUATION_ROUTED
    complaint = schema["properties"]["chief_complaint"]["properties"]["chief_complaint"]
    assert set(complaint["properties"]["quotes"]["items"]["properties"]) == {
        "words",
        "segment_ids",
    }
    assert schema["properties"]["psychiatric_ros"]["properties"]["sleep"] == {"type": "string"}
    assert schema["properties"]["hpi"]["properties"]["symptoms"]["type"] == "array"


# ---------------------------------------------------------------------------
# Through the generation service
# ---------------------------------------------------------------------------

SIDE_TITLES = (SCHEMA_TITLE, RISK_TITLE, EXTRACTION_TITLE)


def _shape(schema: dict[str, Any]) -> Any:
    kind = schema.get("type")
    if kind == "object":
        return {k: _shape(v) for k, v in schema.get("properties", {}).items()}
    if kind == "array":
        return []
    return "Drafted by the model." if kind == "string" else None


@dataclass
class _ScriptedGateway(StructuredLLMGateway):
    """Answers each call by the schema it sends, and records what each was asked."""

    hpi: dict[str, Any] | Exception | None = None
    calls: list[dict[str, Any]] = field(default_factory=list)
    lock: threading.Lock = field(default_factory=threading.Lock)
    hpi_started: threading.Event = field(default_factory=threading.Event)

    def complete_structured(self, **kwargs: Any) -> StructuredCompletion:
        with self.lock:
            self.calls.append(kwargs)
        schema = kwargs["response_schema"]
        title = schema.get("title")
        if title == EXTRACTION_TITLE:
            return StructuredCompletion(data={"statements": []})
        if title == SCHEMA_TITLE:
            self.hpi_started.set()
            if isinstance(self.hpi, Exception):
                raise self.hpi
            return StructuredCompletion(data=self.hpi or _shape(schema))
        if title == RISK_TITLE:
            return StructuredCompletion(data=_shape(schema))
        # The main draft waits for the history call to start: they run side by side.
        assert self.hpi_started.wait(timeout=5), "the history call did not start beside the draft"
        return StructuredCompletion(data=_shape(schema))

    def call(self, title: str | None) -> dict[str, Any]:
        """The call whose schema has ``title``; ``None`` for the main draft."""
        return next(
            c
            for c in self.calls
            if (c["response_schema"].get("title") or None) == title
            or (title is None and c["response_schema"].get("title") not in SIDE_TITLES)
        )


TRANSCRIPT = Transcript(
    format="txt",
    content="\n".join(SEGMENTS.values())
    + "\n[00:20] Therapist: Note for the record. Mood low, sleep intact.",
)


def _draft(
    gateway: _ScriptedGateway,
    template: str = "psychiatric_follow_up",
    current_note: dict[str, Any] | None = None,
) -> dict[str, Any]:
    definition = _definition(template)
    service = RegistryNoteGenerationService(llm_gateway=gateway, model="scripted")
    return service.generate_note(
        definition.key,
        TRANSCRIPT,
        PATIENT,
        NOW,
        inputs={"place_of_service": "In office"},
        definition=definition,
        chart=ChartContext(),
        client_present_end_seconds=0,
        current_note=current_note,
    ).content


@pytest.mark.parametrize(
    ("template", "sections", "rule"),
    [
        ("psychiatric_follow_up", FOLLOW_UP_ROUTED, "Not discussed"),
        ("psychiatric_evaluation", EVALUATION_ROUTED, "review-of-systems"),
    ],
)
def test_the_main_call_carries_no_history_field_and_no_rule_about_one(
    template: str, sections: set[str], rule: str
) -> None:
    gateway = _ScriptedGateway()
    _draft(gateway, template)
    main = gateway.call(None)
    assert not sections & set(main["response_schema"]["properties"])
    assert "assessment" in main["response_schema"]["properties"]
    prompt = main["user_prompt"]
    assert rule not in prompt
    assert "pertinent positives and negatives" not in prompt
    assert "ideally a short quotation" not in prompt
    hpi = gateway.call(SCHEMA_TITLE)
    assert set(hpi["response_schema"]["properties"]) == sections
    assert "pertinent positives and negatives" in hpi["user_prompt"]


def test_the_main_call_is_told_the_history_is_written_apart() -> None:
    """With the history gone from its fields, the main draft once wrote a long symptom
    review into the psychotherapy block of a visit that had no therapy. That block is
    no longer the main draft's either, so it is told nothing about therapy."""
    gateway = _ScriptedGateway()
    _draft(gateway)
    prompt = gateway.call(None)["user_prompt"]
    assert "The note's other sections (Subjective, Risk assessment, Mental status exam, " in prompt
    assert "asking how the client has been" in prompt
    assert "therapy portion" not in prompt


def test_the_history_call_drafts_its_fields_into_the_note_with_the_quote_checked() -> None:
    gateway = _ScriptedGateway(
        hpi={
            "subjective": {
                "chief_complaint": {
                    "text": REASON,
                    "quotes": [
                        _quote("through the roof", [1]),
                        _quote("I feel hopeless", [3]),
                    ],
                },
                "depression": "Low mood and loss of interest; mood observed as low.",
                "mania": "",
            }
        }
    )
    content = _draft(gateway)
    assert content["subjective"]["chief_complaint"] == (
        f'{REASON} The client said: "through the roof"'
    )
    assert content["subjective"]["depression"] == (
        "Low mood and loss of interest; mood observed as low."
    )
    assert content["subjective"]["mania"] == NOT_DISCUSSED
    assert content["assessment"]["formulation"] == "Drafted by the model."
    assert {c["response_schema"].get("title") for c in gateway.calls} == {
        *SIDE_TITLES,
        None,
    }


def test_a_failed_history_call_fails_the_draft_and_logs_its_own_event(
    caplog: pytest.LogCaptureFixture,
) -> None:
    gateway = _ScriptedGateway(hpi=ValueError("schema refused"))
    with caplog.at_level("WARNING"), pytest.raises(ValueError, match="Note generation failed"):
        _draft(gateway)
    (record,) = [r for r in caplog.records if getattr(r, "event", None) == HPI_SECTION_FAILED_EVENT]
    assert record.field_count == 12  # type: ignore[attr-defined]
    assert record.error_class == "ValueError"  # type: ignore[attr-defined]
    assert "through the roof" not in record.getMessage()


def test_the_history_call_runs_on_its_own_model_when_one_is_named(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = Settings(ai_model="note-model", ai_models={"note_generation.hpi": "hpi-model"})
    monkeypatch.setattr(note_generation_service, "get_settings", lambda: settings)
    gateway = _ScriptedGateway()
    service = RegistryNoteGenerationService(llm_gateway=gateway)
    definition = _definition()
    service.generate_note(
        definition.key,
        TRANSCRIPT,
        PATIENT,
        NOW,
        inputs={"place_of_service": "In office"},
        definition=definition,
        chart=ChartContext(),
        client_present_end_seconds=0,
    )
    assert gateway.call(SCHEMA_TITLE)["model"] == "hpi-model"
    assert gateway.call(RISK_TITLE)["model"] == "note-model"
    assert gateway.call(None)["model"] == "note-model"


def test_a_redraft_gives_the_history_call_its_own_fields_of_the_current_note() -> None:
    gateway = _ScriptedGateway()
    current = {
        "subjective": {"depression": "Mood brighter since the dose change."},
        "assessment": {"formulation": "Depression, improving."},
    }
    _draft(gateway, current_note=current)
    hpi_prompt = gateway.call(SCHEMA_TITLE)["user_prompt"]
    main_prompt = gateway.call(None)["user_prompt"]
    assert "Mood brighter since the dose change." in hpi_prompt
    assert "Depression, improving." not in hpi_prompt
    assert "Depression, improving." in main_prompt
    assert "Mood brighter" not in main_prompt
