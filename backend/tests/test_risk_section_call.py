# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""The risk, mental status and measures call, with no model involved.

The composition is pure: a quotation of the client is kept only when every
line it cites contains its words, and is then copied from the line. The
routing reads the definition. Through the generation service a scripted
gateway answers each call by the schema it sends, so what each call was asked
can be read back.
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
from app.notes.registry import NoteFieldDef, NoteSectionDef, NoteTypeDefinition
from app.notes.section_calls import (
    RISK_MSE,
    SectionField,
    model_key,
    section_call_fields,
    without_fields,
)
from app.notes.spec_templates import TEMPLATES_DIR
from app.services import note_generation_service
from app.services.chart_field_extraction import SCHEMA_TITLE as EXTRACTION_TITLE
from app.services.hpi_section_call import SCHEMA_TITLE as HPI_TITLE
from app.services.note_generation_service import (
    RISK_SECTION_FAILED_EVENT,
    RegistryNoteGenerationService,
)
from app.services.risk_section_call import (
    SCHEMA_TITLE,
    build_prompt,
    compose,
    compose_quoted,
    response_schema,
    verified_words,
)
from app.services.structured_llm_gateway import StructuredCompletion, StructuredLLMGateway
from app.settings import Settings

NOW = datetime(2026, 10, 9, 15, tzinfo=UTC)
PATIENT = Patient(id="p", first_name="", last_name="", created_at=NOW, updated_at=NOW)

CURLY = chr(0x2019)
"""A right single quotation mark, as a transcript may write an apostrophe."""
SEGMENTS = {
    0: "[00:01] Therapist: Any thoughts of hurting yourself or that you'd be better off dead?",
    1: "[00:05] Client: Some nights I think everyone would be better off without me.",
    2: "[00:09] Therapist: Any thoughts of hurting anyone else?",
    3: f"[00:11] Client: No, I haven{CURLY}t. Nothing like that.",
}
ASKED_SI = "Asked about thoughts of harming self"
ROUTED = {"risk", "mse", "measures"}


def _spec(template: str) -> PracticeNoteTypeSpec:
    raw = json.loads((TEMPLATES_DIR / f"{template}.json").read_text())["spec"]
    return PracticeNoteTypeSpec.model_validate(raw)


def _definition(template: str = "psychiatric_follow_up") -> NoteTypeDefinition:
    return to_definition(f"custom.{template}", 1, _spec(template))


def _quote(words: str, ids: Any, asked: str = ASKED_SI) -> dict[str, Any]:
    return {"asked": asked, "words": words, "segment_ids": ids}


# ---------------------------------------------------------------------------
# A quotation is kept only when the lines it cites hold its words
# ---------------------------------------------------------------------------


def test_a_quotation_its_line_holds_is_kept_after_the_finding_and_framed_as_the_clients() -> None:
    text = compose_quoted(
        "Passive suicidal ideation, no intent or plan.",
        [_quote("everyone would be better off without me", [1])],
        SEGMENTS,
        "client",
    )
    assert text == (
        "Passive suicidal ideation, no intent or plan. "
        'Asked about thoughts of harming self, the client said: "everyone would be better '
        'off without me"'
    )


def test_a_kept_quotation_is_the_lines_own_words_whatever_the_reply_changed() -> None:
    """Case, spacing and apostrophes come from the transcript, never the reply."""
    words = verified_words("NO,  i haven't.", [3], SEGMENTS)
    assert words == f"No, I haven{CURLY}t."
    parts = verified_words("No, I haven't ... like that", [3], SEGMENTS)
    assert parts == f"No, I haven{CURLY}t ... like that"


def test_a_quotation_its_line_does_not_hold_is_dropped_and_the_question_stays_unquoted() -> None:
    """A paraphrase passed off as the client's words never reaches the note."""
    text = compose_quoted(
        "",
        [_quote("I sometimes wish I were gone", [1])],
        SEGMENTS,
        "client",
    )
    assert text == "Asked about thoughts of harming self."
    assert '"' not in text


def test_a_quotation_that_cites_no_line_or_a_line_the_visit_lacks_is_dropped() -> None:
    for ids in ([], None, [9], [True], ["1"]):
        assert verified_words("better off without me", ids, SEGMENTS) is None
        assert '"' not in compose_quoted("", [_quote("better off without me", ids)], SEGMENTS, "c")


def test_every_line_a_quotation_cites_must_hold_its_words() -> None:
    """Citing the question beside the answer is not evidence that the client said it."""
    assert verified_words("better off without me", [1, 2], SEGMENTS) is None
    assert verified_words("better off", [0, 1], SEGMENTS) == "better off"


def test_not_stated_gives_way_to_a_kept_quotation_and_stays_without_one() -> None:
    kept = compose_quoted("Not stated.", [_quote("Nothing like that", [3])], SEGMENTS, "client")
    assert kept == 'Asked about thoughts of harming self, the client said: "Nothing like that"'
    assert compose_quoted("Not stated.", [], SEGMENTS, "client") == "Not stated."


def test_a_quotation_is_framed_with_the_charts_word_for_the_person() -> None:
    text = compose_quoted("", [_quote("Nothing like that", [3], asked="")], SEGMENTS, "patient")
    assert text == 'The patient said: "Nothing like that"'


def test_mental_status_and_measures_are_the_reply_as_written() -> None:
    fields = section_call_fields(_definition(), RISK_MSE)
    reply = {
        "risk": {"overall_risk": {"text": "Overall acute risk is low.", "quotes": []}},
        "mse": {"speech": "Normal rate and volume."},
        "measures": {"measures_reviewed": "PHQ-9 of 12 on Friday."},
    }
    content = compose(reply, fields, SEGMENTS, "client")
    assert content["mse"]["speech"] == "Normal rate and volume."
    assert content["measures"]["measures_reviewed"] == "PHQ-9 of 12 on Friday."
    assert content["risk"]["overall_risk"] == "Overall acute risk is low."
    # A field the reply left out comes back empty, as the main call's do.
    assert content["mse"]["orientation"] == ""
    assert content["risk"]["safety_plan"] == ""


# ---------------------------------------------------------------------------
# Routing
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("template", ["psychiatric_follow_up", "psychiatric_evaluation"])
def test_both_prescriber_templates_route_their_risk_mental_status_and_measures(
    template: str,
) -> None:
    definition = _definition(template)
    routed = section_call_fields(definition, RISK_MSE)
    assert {f.section for f in routed} == ROUTED
    for section in definition.sections:
        if section.key in ROUTED:
            assert {f.field.key for f in routed if f.section == section.key} == set(
                section.field_keys()
            )
    # A risk field gives the client's words apart; mental status and measures do not.
    assert {f.path for f in routed if f.quotes} == {
        f"risk.{k}" for k in definition.sections[_index(definition, "risk")].field_keys()
    }
    assert len(routed) <= 15


def _index(definition: NoteTypeDefinition, key: str) -> int:
    return definition.section_keys().index(key)


def test_a_field_a_practice_adds_to_a_based_types_risk_section_is_drafted_by_the_risk_call() -> (
    None
):
    patch = NoteTypePatch(
        add_fields=[
            {
                "section": "risk",
                "field": {
                    "key": "firearm_access",
                    "label": "Firearm access",
                    "ai_hint": "What was asked and answered about firearms at home.",
                },
            },
            {
                "section": "plan",
                "field": {"key": "shared_decision", "label": "Shared decision"},
            },
        ]
    )
    based = to_definition("custom.based", 1, resolve_spec(_spec("psychiatric_follow_up"), patch))
    routed = {f.path: f for f in section_call_fields(based, RISK_MSE)}
    assert routed["risk.firearm_access"].quotes
    assert "plan.shared_decision" not in routed
    main = without_fields(based, list(routed.values()))
    assert not ROUTED & set(main.section_keys())
    assert "shared_decision" in main.sections[_index(main, "plan")].field_keys()


def test_a_format_written_in_code_keeps_one_call() -> None:
    """Only a type written as a spec is routed; SOAP and the like have no spec."""
    risk = NoteSectionDef("risk", "Risk", (NoteFieldDef("risk_summary", "Risk", "text"),))
    coded = NoteTypeDefinition(key="coded", label="Coded", description="", sections=(risk,))
    assert section_call_fields(coded, RISK_MSE) == []


def test_a_field_of_another_kind_stays_with_the_main_call() -> None:
    spec = PracticeNoteTypeSpec.model_validate(
        {
            "label": "Visit",
            "sections": [
                {
                    "key": "mse",
                    "label": "MSE",
                    "fields": [
                        {"key": "speech", "label": "Speech"},
                        {"key": "screens", "label": "Screens", "kind": "list"},
                        {"key": "coded", "label": "Coded", "kind": "diagnoses"},
                    ],
                }
            ],
        }
    )
    routed = section_call_fields(to_definition("custom.v", 1, spec), RISK_MSE)
    assert [f.field.key for f in routed] == ["speech", "screens"]


def test_the_risk_call_has_its_own_model_key() -> None:
    assert model_key(RISK_MSE) == "note_generation.risk_mse"


# ---------------------------------------------------------------------------
# The prompt and the schema
# ---------------------------------------------------------------------------


def test_the_instruction_is_short_and_uses_the_charts_word_for_the_person() -> None:
    fields = section_call_fields(_definition(), RISK_MSE)
    prompt = build_prompt(fields, "patient", "[S0] [00:01] Patient: Fine.")
    instruction = prompt.split("\n\nFields:", 1)[0]
    assert len(instruction.splitlines()) <= 12
    assert "the patient's last line" in instruction
    assert "client" not in instruction.lower()
    # Each field with its hint; the transcript numbered.
    assert "- risk.overall_risk (Overall acute risk; text and quotes): Only the clinician" in prompt
    assert "- mse.speech (Speech): Rate, rhythm" in prompt
    assert prompt.endswith("Transcript (each line numbered [Sn]):\n[S0] [00:01] Patient: Fine.")


def test_the_schema_asks_for_a_risk_fields_quotes_apart_and_plain_findings_elsewhere() -> None:
    fields = section_call_fields(_definition(), RISK_MSE)
    schema = response_schema(fields)
    assert schema["title"] == SCHEMA_TITLE
    assert set(schema["properties"]) == ROUTED
    ideation = schema["properties"]["risk"]["properties"]["suicidal_homicidal_ideation"]
    quote = ideation["properties"]["quotes"]["items"]["properties"]
    assert set(quote) == {"asked", "words", "segment_ids"}
    assert schema["properties"]["mse"]["properties"]["speech"] == {"type": "string"}


# ---------------------------------------------------------------------------
# Through the generation service
# ---------------------------------------------------------------------------


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

    risk: dict[str, Any] | Exception | None = None
    calls: list[dict[str, Any]] = field(default_factory=list)
    lock: threading.Lock = field(default_factory=threading.Lock)
    risk_started: threading.Event = field(default_factory=threading.Event)

    def complete_structured(self, **kwargs: Any) -> StructuredCompletion:
        with self.lock:
            self.calls.append(kwargs)
        schema = kwargs["response_schema"]
        title = schema.get("title")
        if title == EXTRACTION_TITLE:
            return StructuredCompletion(data={"statements": []})
        if title == HPI_TITLE:
            return StructuredCompletion(data=_shape(schema))
        if title == SCHEMA_TITLE:
            self.risk_started.set()
            if isinstance(self.risk, Exception):
                raise self.risk
            return StructuredCompletion(data=self.risk or _shape(schema))
        # The main draft waits for the risk call to start: they run side by side.
        assert self.risk_started.wait(timeout=5), "the risk call did not start beside the draft"
        return StructuredCompletion(data=_shape(schema))

    def call(self, title: str | None) -> dict[str, Any]:
        return next(
            c
            for c in self.calls
            if c["response_schema"].get("title") == title
            or (
                title is None
                and c["response_schema"].get("title")
                not in (SCHEMA_TITLE, EXTRACTION_TITLE, HPI_TITLE)
            )
        )


TRANSCRIPT = Transcript(
    format="txt",
    content="\n".join(SEGMENTS.values())
    + "\n[00:20] Therapist: Note for the record. Overall acute risk is low.",
)


def _draft(
    gateway: _ScriptedGateway,
    definition: NoteTypeDefinition | None = None,
    current_note: dict[str, Any] | None = None,
) -> dict[str, Any]:
    definition = definition or _definition()
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


def test_the_main_call_no_longer_asks_for_or_sees_risk_mental_status_or_measures() -> None:
    gateway = _ScriptedGateway()
    _draft(gateway)
    main = gateway.call(None)
    assert not ROUTED & set(main["response_schema"]["properties"])
    assert {"assessment", "plan"} <= set(main["response_schema"]["properties"])
    fields_block = main["user_prompt"]
    for hint in ("Only the clinician's own stated judgment of overall acute risk", "Rate, rhythm"):
        assert hint not in fields_block
    risk = gateway.call(SCHEMA_TITLE)
    assert set(risk["response_schema"]["properties"]) == ROUTED


def test_the_risk_call_drafts_its_fields_into_the_note_with_quotes_checked() -> None:
    gateway = _ScriptedGateway(
        risk={
            "risk": {
                "suicidal_homicidal_ideation": {
                    "text": "Passive suicidal ideation.",
                    "quotes": [
                        _quote("better off without me", [1]),
                        _quote("I would never", [3], asked="Asked about harming others"),
                    ],
                },
                "overall_risk": {"text": "Overall acute risk is low.", "quotes": []},
            },
            "mse": {"speech": "Normal rate."},
        }
    )
    content = _draft(gateway)
    assert content["risk"]["suicidal_homicidal_ideation"] == (
        "Passive suicidal ideation. Asked about thoughts of harming self, the client said: "
        '"better off without me" Asked about harming others.'
    )
    assert content["risk"]["overall_risk"] == "Overall acute risk is low."
    assert content["mse"]["speech"] == "Normal rate."
    assert content["subjective"]["chief_complaint"] == "Drafted by the model."
    assert {c["response_schema"].get("title") for c in gateway.calls} == {
        EXTRACTION_TITLE,
        SCHEMA_TITLE,
        HPI_TITLE,
        None,
    }


def test_a_failed_risk_call_fails_the_draft_and_logs_its_own_event(
    caplog: pytest.LogCaptureFixture,
) -> None:
    gateway = _ScriptedGateway(risk=ValueError("schema refused"))
    with caplog.at_level("WARNING"), pytest.raises(ValueError, match="Note generation failed"):
        _draft(gateway)
    (record,) = [
        r for r in caplog.records if getattr(r, "event", None) == RISK_SECTION_FAILED_EVENT
    ]
    assert record.field_count == 14  # type: ignore[attr-defined]
    assert record.error_class == "ValueError"  # type: ignore[attr-defined]
    assert "better off" not in record.getMessage()


def test_the_risk_call_runs_on_its_own_model_when_one_is_named(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = Settings(ai_model="note-model", ai_models={"note_generation.risk_mse": "risk-model"})
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
    assert gateway.call(SCHEMA_TITLE)["model"] == "risk-model"
    assert gateway.call(None)["model"] == "note-model"

    unnamed = Settings(ai_model="note-model")
    monkeypatch.setattr(note_generation_service, "get_settings", lambda: unnamed)
    assert service._resolve_model(RISK_MSE) == "note-model"


def test_a_redraft_gives_each_call_its_own_fields_of_the_current_note() -> None:
    gateway = _ScriptedGateway()
    current = {
        "risk": {"overall_risk": "Overall acute risk is low."},
        "mse": {"speech": "Pressured."},
        "assessment": {"formulation": "Worse sleep."},
    }
    _draft(gateway, current_note=current)
    risk_prompt = gateway.call(SCHEMA_TITLE)["user_prompt"]
    main_prompt = gateway.call(None)["user_prompt"]
    assert "Pressured." in risk_prompt
    assert "Overall acute risk is low." in risk_prompt
    assert "Worse sleep." not in risk_prompt
    assert "Worse sleep." in main_prompt
    assert "Pressured." not in main_prompt


def test_the_main_calls_addendum_no_longer_places_risk_or_mental_status() -> None:
    """With those sections drafted apart, the main draft has no field to put them in."""
    gateway = _ScriptedGateway()
    _draft(gateway)
    addendum = gateway.call(None)["user_prompt"].split("Clinician addendum:", 1)[1]
    assert "Where the addendum states a prescription monitoring check" in addendum
    assert "risk, mental status" not in addendum


def test_section_field_paths_name_section_and_key() -> None:
    f = SectionField("risk", NoteFieldDef("overall_risk", "Overall", "text"), quotes=True)
    assert f.path == "risk.overall_risk"
