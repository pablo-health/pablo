# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""The psychotherapy call, with no model involved.

Whether the block is drafted at all is decided in code: turns labelled with no
therapy and no dictated time leave it empty, and no model is asked to write it.
The composition is pure: the response's quotation of the client is kept only
when every line it cites contains its words. Through the generation service a
scripted gateway answers each call by the schema it sends, so which calls ran,
and what each was asked, can be read back.
"""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import pytest
from app.models import Patient, Transcript
from app.notes.chart_context import ChartContext
from app.notes.note_type_patch import resolve_spec
from app.notes.practice_spec import NoteTypePatch
from app.notes.practice_types import PracticeNoteTypeSpec, to_definition
from app.notes.section_calls import (
    HPI,
    PSYCHOTHERAPY,
    RISK_MSE,
    code_written_fields,
    model_key,
    section_call_fields,
)
from app.notes.spec_templates import TEMPLATES_DIR
from app.services import note_generation_service
from app.services.chart_field_extraction import SCHEMA_TITLE as EXTRACTION_TITLE
from app.services.hpi_section_call import SCHEMA_TITLE as HPI_TITLE
from app.services.note_generation_service import (
    PSYCHOTHERAPY_SECTION_FAILED_EVENT,
    RegistryNoteGenerationService,
)
from app.services.psychotherapy_section_call import (
    SCHEMA_TITLE,
    SYSTEM_PROMPT,
    build_prompt,
    compose,
    empty_block,
    no_therapy,
    response_schema,
)
from app.services.risk_section_call import SCHEMA_TITLE as RISK_TITLE
from app.services.structured_llm_gateway import StructuredCompletion, StructuredLLMGateway
from app.services.therapy_labels import TIME_KEY
from app.settings import Settings

if TYPE_CHECKING:
    from app.notes.registry import NoteTypeDefinition
    from app.notes.section_calls import SectionField

NOW = datetime(2026, 10, 9, 15, tzinfo=UTC)
PATIENT = Patient(id="p", first_name="", last_name="", created_at=NOW, updated_at=NOW)

CURLY = chr(0x2019)
SEGMENTS = {
    0: "[00:00:01] Therapist: How did the thought record go this week?",
    1: f"[00:00:20] Client: I did it four times. My worry{CURLY}s down to a five.",
    2: "[00:01:00] Therapist: What was the evidence against the thought?",
    3: "[00:01:30] Client: That I have never actually been fired.",
}
TRANSCRIPT = Transcript(format="txt", content="\n".join(SEGMENTS.values()))

THERAPY_FIELDS = (
    "issues_addressed",
    "modality_interventions",
    "response",
    "goal_plan",
    "progress",
    "therapy_cadence",
)
EVALUATION_FIELDS = ("modality_interventions", "response", "goal_plan")


def _spec(template: str) -> PracticeNoteTypeSpec:
    raw = json.loads((TEMPLATES_DIR / f"{template}.json").read_text())["spec"]
    return PracticeNoteTypeSpec.model_validate(raw)


def _definition(template: str = "psychiatric_follow_up") -> NoteTypeDefinition:
    return to_definition(f"custom.{template}", 1, _spec(template))


def _fields(template: str = "psychiatric_follow_up") -> list[SectionField]:
    return section_call_fields(_definition(template), PSYCHOTHERAPY)


def _quote(words: str, ids: Any) -> dict[str, Any]:
    return {"words": words, "segment_ids": ids}


def _reply(**fields: Any) -> dict[str, Any]:
    return {"psychotherapy": {k: fields.get(k, "") for k in THERAPY_FIELDS}}


# ---------------------------------------------------------------------------
# The gate: no therapy, no call
# ---------------------------------------------------------------------------

NO_THERAPY = {1.0: "medication_management", 20.0: "medication_management", 60.0: "admin"}
SOME_THERAPY = {**NO_THERAPY, 60.0: "therapy"}


def test_turns_labelled_with_no_therapy_and_no_dictated_time_close_the_gate() -> None:
    assert no_therapy(NO_THERAPY, dictated_time=False, current_block=False)


def test_one_therapy_turn_a_dictated_time_or_a_redrafted_block_opens_it() -> None:
    assert not no_therapy(SOME_THERAPY, dictated_time=False, current_block=False)
    assert not no_therapy(NO_THERAPY, dictated_time=True, current_block=False)
    assert not no_therapy(NO_THERAPY, dictated_time=False, current_block=True)


@pytest.mark.parametrize("labels", [{}, None])
def test_labels_that_could_not_be_made_decide_nothing(labels: Any) -> None:
    """No timed turns, or a failed labelling call: the call drafts, as before."""
    assert not no_therapy(labels, dictated_time=False, current_block=False)


# ---------------------------------------------------------------------------
# The response: the client's words only when the line holds them
# ---------------------------------------------------------------------------

RESPONSE = "Completed the thought record four times; rated worry lower."


def test_a_response_quotation_its_line_holds_is_kept_after_the_text() -> None:
    reply = _reply(
        modality_interventions="Reviewed the thought record.",
        response={"text": RESPONSE, "quotes": [_quote("my worry's down to a five", [1])]},
    )
    content = compose(reply, _fields(), SEGMENTS, "client")
    # Copied from the line: its case and its apostrophe, not the reply's.
    assert content["psychotherapy"]["response"] == (
        f'{RESPONSE} The client said: "My worry{CURLY}s down to a five"'
    )


@pytest.mark.parametrize("ids", [[], None, [9], [0], [0, 1]])
def test_a_response_quotation_its_lines_do_not_hold_is_dropped_and_the_text_stands(
    ids: Any,
) -> None:
    """A paraphrase, a line the visit lacks, or the clinician's line cited beside the
    client's: none is the client's words, and none reaches the note."""
    reply = _reply(
        modality_interventions="Reviewed the thought record.",
        response={"text": RESPONSE, "quotes": [_quote("down to a five", ids)]},
    )
    content = compose(reply, _fields(), SEGMENTS, "client")
    assert content["psychotherapy"]["response"] == RESPONSE


def test_the_response_frames_a_quotation_with_the_charts_word_for_the_person() -> None:
    reply = _reply(response={"text": "", "quotes": [_quote("never actually been fired", [3])]})
    content = compose(reply, _fields(), SEGMENTS, "patient")
    assert content["psychotherapy"]["response"] == ('The patient said: "never actually been fired"')


def test_a_field_the_therapy_did_not_cover_reads_not_stated() -> None:
    content = compose(
        _reply(modality_interventions="Reviewed the thought record."),
        _fields(),
        SEGMENTS,
        "client",
    )
    block = content["psychotherapy"]
    assert block["modality_interventions"] == "Reviewed the thought record."
    assert block["goal_plan"] == "Not stated."
    assert block["therapy_cadence"] == "Not stated."
    assert block["issues_addressed"] == "Not stated."


@pytest.mark.parametrize("empty", ["", "Not stated.", None])
def test_a_reply_that_wrote_nothing_leaves_the_whole_block_empty(empty: Any) -> None:
    """The call's own word that no therapy took place: an empty block, not six
    "Not stated." lines on a visit with no therapy."""
    reply = {"psychotherapy": dict.fromkeys(THERAPY_FIELDS, empty)}
    reply["psychotherapy"]["response"] = {"text": empty, "quotes": []}
    content = compose(reply, _fields(), SEGMENTS, "client")
    assert content == empty_block(_fields())
    assert set(content["psychotherapy"].values()) == {""}


# ---------------------------------------------------------------------------
# Routing
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("template", "keys"),
    [("psychiatric_follow_up", THERAPY_FIELDS), ("psychiatric_evaluation", EVALUATION_FIELDS)],
)
def test_both_prescriber_templates_route_their_psychotherapy_block_but_its_time(
    template: str, keys: tuple[str, ...]
) -> None:
    definition = _definition(template)
    routed = section_call_fields(definition, PSYCHOTHERAPY)
    assert [f.path for f in routed] == [f"psychotherapy.{k}" for k in keys]
    # Only the response carries the client's words.
    assert [f.field.key for f in routed if f.quotes] == ["response"]
    # The time is code's: no call drafts it, and the main draft is not asked for it.
    assert [f.path for f in code_written_fields(definition)] == ["psychotherapy.psychotherapy_time"]
    others = {f.path for call in (HPI, RISK_MSE) for f in section_call_fields(definition, call)}
    assert not others & {f.path for f in routed}


def test_a_field_a_practice_adds_to_a_based_types_psychotherapy_is_drafted_by_this_call() -> None:
    patch = NoteTypePatch(
        add_fields=[
            {
                "section": "psychotherapy",
                "field": {"key": "homework", "label": "Homework", "ai_hint": "As assigned."},
            },
        ],
        hide_fields=["psychotherapy.progress"],
    )
    based = to_definition("custom.based", 1, resolve_spec(_spec("psychiatric_follow_up"), patch))
    routed = {f.path for f in section_call_fields(based, PSYCHOTHERAPY)}
    assert "psychotherapy.homework" in routed
    assert "psychotherapy.progress" not in routed
    assert "psychotherapy.psychotherapy_time" not in routed


def test_the_psychotherapy_call_has_its_own_model_key() -> None:
    assert model_key(PSYCHOTHERAPY) == "note_generation.psychotherapy"


# ---------------------------------------------------------------------------
# The prompt and the schema
# ---------------------------------------------------------------------------


def test_the_instruction_is_short_and_uses_the_charts_word_for_the_person() -> None:
    prompt = build_prompt(_fields(), "patient", "[S0] [00:00:01] Patient: Fine.")
    instruction = prompt.split("\n\nFields:", 1)[0]
    assert len(instruction.splitlines()) <= 12
    assert "client" not in instruction.lower()
    assert "the patient's own key words" in instruction
    assert "- psychotherapy.response (Response; text and quotes): " in prompt
    assert prompt.endswith("Transcript (each line numbered [Sn]):\n[S0] [00:00:01] Patient: Fine.")


def test_the_instruction_carries_the_hints_rules_and_never_asks_for_a_code_or_a_time() -> None:
    instruction = build_prompt(_fields(), "client", "").split("\n\nFields:", 1)[0]
    for rule in (
        "Name a technique only when the clinician named it or did its steps",
        "a rating or a count only as the client said it",
        "at most two of the client's own key words",
        "Progress only where a rating moved or an earlier assignment was done",
        "The goal only as the clinician or the client stated it",
        "Cadence only as the clinician said",
        "Never a billing code, a clock time or a number of minutes",
        "If no psychotherapy took place",
    ):
        assert rule in instruction
    for text in (SYSTEM_PROMPT, instruction):
        assert "addendum" not in text.lower()


def test_the_schema_asks_for_the_responses_quotes_apart_and_never_for_the_time() -> None:
    schema = response_schema(_fields())
    assert schema["title"] == SCHEMA_TITLE
    block = schema["properties"]["psychotherapy"]["properties"]
    assert list(block) == list(THERAPY_FIELDS)
    assert set(block["response"]["properties"]["quotes"]["items"]["properties"]) == {
        "words",
        "segment_ids",
    }
    assert block["goal_plan"] == {"type": "string"}


# ---------------------------------------------------------------------------
# Through the generation service
# ---------------------------------------------------------------------------

SIDE_TITLES = (SCHEMA_TITLE, HPI_TITLE, RISK_TITLE, EXTRACTION_TITLE)


def _shape(schema: dict[str, Any]) -> Any:
    kind = schema.get("type")
    if kind == "object":
        return {k: _shape(v) for k, v in schema.get("properties", {}).items()}
    if kind == "array":
        return []
    return "Drafted by the model." if kind == "string" else None


def _is_labels(call: dict[str, Any]) -> bool:
    return "runs" in call["response_schema"].get("properties", {})


def _runs(*labels: str) -> dict[str, Any]:
    return {
        "runs": [
            {"first_segment": i, "last_segment": i, "label": label}
            for i, label in enumerate(labels)
        ],
        "cue_segment": -1,
    }


@dataclass
class _ScriptedGateway(StructuredLLMGateway):
    """Answers each call by the schema it sends, and records what each was asked."""

    labels: dict[str, Any] | Exception = field(
        default_factory=lambda: _runs("therapy", "therapy", "therapy", "therapy")
    )
    therapy: dict[str, Any] | Exception | None = None
    stated: dict[str, Any] | None = None
    calls: list[dict[str, Any]] = field(default_factory=list)
    lock: threading.Lock = field(default_factory=threading.Lock)

    def complete_structured(self, **kwargs: Any) -> StructuredCompletion:
        with self.lock:
            self.calls.append(kwargs)
        schema = kwargs["response_schema"]
        title = schema.get("title")
        if title == EXTRACTION_TITLE:
            return StructuredCompletion(data={"statements": []})
        if "runs" in schema.get("properties", {}):
            if isinstance(self.labels, Exception):
                raise self.labels
            return StructuredCompletion(data=self.labels)
        if title == SCHEMA_TITLE:
            if isinstance(self.therapy, Exception):
                raise self.therapy
            return StructuredCompletion(data=self.therapy or _shape(schema))
        data = _shape(schema)
        if TIME_KEY in schema.get("properties", {}):
            data[TIME_KEY] = self.stated or {}
        return StructuredCompletion(data=data)

    def titled(self, title: str) -> list[dict[str, Any]]:
        return [c for c in self.calls if c["response_schema"].get("title") == title]

    def main(self) -> dict[str, Any]:
        return next(
            c
            for c in self.calls
            if c["response_schema"].get("title") not in SIDE_TITLES and not _is_labels(c)
        )


def _generate(
    gateway: _ScriptedGateway,
    template: str = "psychiatric_follow_up",
    *,
    inputs: dict[str, str] | None = None,
    current_note: dict[str, Any] | None = None,
    client_present_end_seconds: float | None = None,
) -> note_generation_service.GeneratedNote:
    definition = _definition(template)
    service = RegistryNoteGenerationService(llm_gateway=gateway, model="scripted")
    return service.generate_note(
        definition.key,
        TRANSCRIPT,
        PATIENT,
        NOW,
        inputs=inputs or {"place_of_service": "In office"},
        definition=definition,
        chart=ChartContext(),
        client_present_end_seconds=client_present_end_seconds,
        current_note=current_note,
    )


def test_a_visit_labelled_with_no_therapy_leaves_the_block_empty_and_asks_no_model() -> None:
    gateway = _ScriptedGateway(labels=_runs("medication_management", "medication_management"))
    generated = _generate(gateway)
    assert gateway.titled(SCHEMA_TITLE) == []
    assert set(generated.content["psychotherapy"].values()) == {""}
    # The labels still reach the proposal: the clinician confirms them as before.
    assert generated.psychotherapy_proposal is not None
    assert {item["label"] for item in generated.psychotherapy_proposal["labels"]} == {
        "medication_management"
    }
    # Labelled before any draft existed: the prompt quotes no drafted block.
    (labels,) = [c for c in gateway.calls if _is_labels(c)]
    assert "psychotherapy section of the note drafted" not in labels["user_prompt"]


def test_a_visit_with_therapy_turns_drafts_the_block_with_its_quote_checked() -> None:
    gateway = _ScriptedGateway(
        labels=_runs("medication_management", "therapy", "therapy", "therapy"),
        therapy=_reply(
            modality_interventions="Weighed the evidence for and against the thought.",
            response={
                "text": RESPONSE,
                "quotes": [_quote("down to a five", [1]), _quote("I feel cured", [3])],
            },
        ),
    )
    generated = _generate(gateway)
    block = generated.content["psychotherapy"]
    assert len(gateway.titled(SCHEMA_TITLE)) == 1
    assert block["modality_interventions"] == "Weighed the evidence for and against the thought."
    assert block["response"] == f'{RESPONSE} The client said: "down to a five"'
    assert block["goal_plan"] == "Not stated."
    # No time dictated: the time field stays code's, and empty.
    assert block["psychotherapy_time"] == ""


def test_a_dictated_time_drafts_the_block_even_when_no_turn_was_labelled_therapy() -> None:
    gateway = _ScriptedGateway(
        labels=_runs("admin", "admin", "admin", "admin"),
        stated={"minutes": 20, "as_dictated": "Psychotherapy 20 minutes."},
    )
    generated = _generate(gateway)
    assert len(gateway.titled(SCHEMA_TITLE)) == 1
    block = generated.content["psychotherapy"]
    assert block["psychotherapy_time"] == "20 minutes"
    assert block["modality_interventions"] == "Drafted by the model."


def test_a_failed_labelling_call_decides_nothing_and_the_block_is_drafted() -> None:
    gateway = _ScriptedGateway(labels=ValueError("labels refused"))
    generated = _generate(gateway)
    assert len(gateway.titled(SCHEMA_TITLE)) == 1
    assert generated.content["psychotherapy"]["issues_addressed"] == "Drafted by the model."


def test_a_visit_with_no_client_present_drafts_no_block_and_labels_nothing() -> None:
    gateway = _ScriptedGateway()
    generated = _generate(gateway, client_present_end_seconds=0)
    assert gateway.titled(SCHEMA_TITLE) == []
    assert not any(_is_labels(c) for c in gateway.calls)
    assert set(generated.content["psychotherapy"].values()) == {""}


def test_a_redraft_of_a_note_with_a_written_block_drafts_it_again() -> None:
    """The labels say no therapy, but the clinician's note has a block: the call keeps it."""
    gateway = _ScriptedGateway(labels=_runs("admin", "admin", "admin", "admin"))
    current = {
        "psychotherapy": {"modality_interventions": "Thought record reviewed."},
        "assessment": {"formulation": "Anxiety, improving."},
    }
    _generate(gateway, current_note=current)
    (call,) = gateway.titled(SCHEMA_TITLE)
    assert "Thought record reviewed." in call["user_prompt"]
    assert "Anxiety, improving." not in call["user_prompt"]
    assert "Thought record reviewed." not in gateway.main()["user_prompt"]


@pytest.mark.parametrize(
    ("template", "rules"),
    [
        (
            "psychiatric_follow_up",
            ("leave every field of the Psychotherapy section empty", "psychotherapy portion"),
        ),
        (
            "psychiatric_evaluation",
            ("The Psychotherapy section is only for", "stays in its own section"),
        ),
    ],
)
def test_the_main_call_carries_no_psychotherapy_field_and_no_rule_about_one(
    template: str, rules: tuple[str, ...]
) -> None:
    gateway = _ScriptedGateway()
    _generate(gateway, template)
    main = gateway.main()
    assert "psychotherapy" not in main["response_schema"]["properties"]
    # The dictated time is still the main draft's to return, apart from the note.
    assert TIME_KEY in main["response_schema"]["properties"]
    prompt = main["system_prompt"] + main["user_prompt"]
    for rule in rules:
        assert rule not in prompt
    for f in _definition(template).sections[-1].fields:
        assert f.ai_hint is not None
        assert f.ai_hint not in prompt
    assert "Name a technique" not in prompt
    assert "therapy portion" not in prompt


def test_the_evaluations_entered_visit_code_reaches_the_psychotherapy_call() -> None:
    """A diagnostic evaluation carries no psychotherapy block: the call reads the code."""
    gateway = _ScriptedGateway()
    _generate(
        gateway,
        "psychiatric_evaluation",
        inputs={
            "place_of_service": "In office",
            "visit_code": "Psychiatric diagnostic evaluation (90792)",
        },
    )
    (call,) = gateway.titled(SCHEMA_TITLE)
    assert "- Visit code: Psychiatric diagnostic evaluation (90792)" in call["user_prompt"]
    assert "psychiatric diagnostic evaluation, leave every field empty" in call["user_prompt"]


def test_the_psychotherapy_call_runs_on_its_own_model_when_one_is_named(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = Settings(
        ai_model="note-model", ai_models={"note_generation.psychotherapy": "therapy-model"}
    )
    monkeypatch.setattr(note_generation_service, "get_settings", lambda: settings)
    gateway = _ScriptedGateway()
    definition = _definition()
    RegistryNoteGenerationService(llm_gateway=gateway).generate_note(
        definition.key,
        TRANSCRIPT,
        PATIENT,
        NOW,
        inputs={"place_of_service": "In office"},
        definition=definition,
        chart=ChartContext(),
    )
    assert gateway.titled(SCHEMA_TITLE)[0]["model"] == "therapy-model"
    assert gateway.main()["model"] == "note-model"


def test_a_failed_psychotherapy_call_fails_the_draft_and_logs_its_own_event(
    caplog: pytest.LogCaptureFixture,
) -> None:
    gateway = _ScriptedGateway(therapy=ValueError("schema refused"))
    with caplog.at_level("WARNING"), pytest.raises(ValueError, match="Note generation failed"):
        _generate(gateway)
    (record,) = [
        r for r in caplog.records if getattr(r, "event", None) == PSYCHOTHERAPY_SECTION_FAILED_EVENT
    ]
    assert record.field_count == len(THERAPY_FIELDS)  # type: ignore[attr-defined]
    assert "down to a five" not in record.getMessage()
