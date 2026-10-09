# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Fields printed from the chart: written in code, with the visit's marks composed in code.

No model runs here. The renderer and composer are pure; the extraction call
is answered by a scripted gateway, so what is tested is what code does with
an answer, including one that cites lines the visit does not have.
"""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import Any

import pytest
from app.chart_history.fields import HISTORY_KEYS, SUBSTANCE_KEYS
from app.models import Patient, Transcript
from app.notes.chart_context import (
    ChartContext,
    ChartHistoryField,
    ChartMedication,
    ChartProblem,
)
from app.notes.chart_fields import (
    ASKED_NO_CHANGE,
    DENIED,
    IN_OFFICE,
    NOT_ASKED,
    NamedDiagnosis,
    Statement,
    Statements,
    compose_all,
    place_of_service,
    rendered_fields,
)
from app.notes.note_type_patch import resolve_spec
from app.notes.practice_spec import NoteTypePatch, PracticeFieldSpec
from app.notes.practice_types import PracticeNoteTypeSpec, to_definition
from app.notes.spec_templates import TEMPLATES_DIR
from app.services.chart_field_extraction import (
    RISK_LINES,
    SCHEMA_TITLE,
    build_prompt,
    parse,
    response_schema,
)
from app.services.note_generation_service import RegistryNoteGenerationService
from app.services.structured_llm_gateway import StructuredCompletion, StructuredLLMGateway
from pydantic import ValidationError

NOW = datetime(2026, 10, 8, 15, tzinfo=UTC)
PATIENT = Patient(id="p", first_name="", last_name="", created_at=NOW, updated_at=NOW)
RECORDED = date(2026, 7, 14)
NON_SUBSTANCE_HISTORY = [k for k in HISTORY_KEYS if k not in SUBSTANCE_KEYS]


def _spec(template: str) -> PracticeNoteTypeSpec:
    raw = json.loads((TEMPLATES_DIR / f"{template}.json").read_text())["spec"]
    return PracticeNoteTypeSpec.model_validate(raw)


def _definition(template: str = "psychiatric_follow_up") -> Any:
    return to_definition(f"custom.{template}", 1, _spec(template))


def _compose(
    chart: ChartContext = ChartContext(),
    statements: Statements = Statements(),
    inputs: dict[str, str] | None = None,
    template: str = "psychiatric_follow_up",
) -> dict[str, dict[str, Any]]:
    return compose_all(
        _definition(template), chart, inputs or {"place_of_service": "In office"}, statements
    )


def _history(*fields: tuple[str, str]) -> tuple[ChartHistoryField, ...]:
    return tuple(ChartHistoryField(k, text, RECORDED) for k, text in fields)


# ---------------------------------------------------------------------------
# Which fields code writes
# ---------------------------------------------------------------------------


def test_the_follow_up_writes_every_chart_fed_field_in_code() -> None:
    rendered = {r.field.key: r.source for r in rendered_fields(_definition())}
    assert set(rendered) == {
        *HISTORY_KEYS,
        "place_of_service",
        "current_medications",
        "allergies",
        "diagnoses",
    }
    assert rendered["diagnoses"] == "problems"
    assert rendered["current_medications"] == "medications"


def test_the_evaluation_drafts_its_history_and_diagnoses_from_the_visit() -> None:
    rendered = {r.field.key for r in rendered_fields(_definition("psychiatric_evaluation"))}
    assert rendered == {"place_of_service", "current_medications", "allergies"}


def test_chart_fedness_comes_from_the_source_not_the_key() -> None:
    """A field keyed like a chart field drafts from the visit unless it names a source;
    a field a practice adds to a base can name one under any key."""
    plain = PracticeNoteTypeSpec.model_validate(
        {
            "label": "Visit",
            "sections": [
                {"key": "s", "label": "S", "fields": [{"key": "allergies", "label": "A"}]}
            ],
        }
    )
    assert rendered_fields(to_definition("custom.plain", 1, plain)) == []

    patch = NoteTypePatch(
        add_fields=[
            {
                "section": "social_history",
                "field": {"key": "home_now", "label": "Home", "source": "living_situation"},
            }
        ]
    )
    based = to_definition("custom.based", 1, resolve_spec(_spec("psychiatric_follow_up"), patch))
    rendered = {r.field.key: r.source for r in rendered_fields(based)}
    assert rendered["home_now"] == "living_situation"

    chart = ChartContext(history=_history(("living_situation", "Lives with a roommate.")))
    content = compose_all(based, chart, {"place_of_service": "In office"}, Statements())
    assert content["social_history"]["home_now"] == "Lives with a roommate."


def test_a_source_is_checked_against_what_code_can_print_and_its_kind() -> None:
    with pytest.raises(ValidationError, match="unknown source"):
        PracticeFieldSpec(key="x", label="X", source="horoscope")
    with pytest.raises(ValidationError, match="takes the 'list' kind"):
        PracticeFieldSpec(key="x", label="X", source="medications")
    with pytest.raises(ValidationError, match="takes the 'diagnoses' kind"):
        PracticeFieldSpec(key="x", label="X", kind="text", source="problems")
    assert PracticeFieldSpec(key="x", label="X", source="alcohol").source == "alcohol"


# ---------------------------------------------------------------------------
# History fields
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("key", NON_SUBSTANCE_HISTORY)
def test_a_history_field_is_the_charts_text_or_not_recorded(key: str) -> None:
    text = "Line one of the chart.\nLine two, kept."
    sections = _compose(ChartContext(history=_history((key, text))))
    printed = {k: v for fields in sections.values() for k, v in fields.items()}
    assert printed[key] == text
    for other in NON_SUBSTANCE_HISTORY:
        if other != key:
            assert printed[other] == "Not recorded"


def test_what_the_visit_stated_follows_the_charts_text_marked() -> None:
    chart = ChartContext(history=_history(("work_school", "Employed at a logistics firm.")))
    statements = Statements(
        fields=(
            Statement("work_school", stated="I got laid off last week"),
            Statement("work_school", stated='"I got laid off last week"'),
            Statement("work_school", stated="starting a course in May"),
            Statement("relationships", stated="we split up"),
        )
    )
    social = _compose(chart, statements)["social_history"]
    assert social["work_school"] == (
        'Employed at a logistics firm. (stated this visit: "I got laid off last week"; '
        '"starting a course in May")'
    )
    assert social["relationships"] == 'Not recorded (stated this visit: "we split up")'
    assert social["supports"] == "Not recorded"


def test_a_statement_with_no_words_adds_no_mark() -> None:
    chart = ChartContext(history=_history(("supports", "Sister nearby.")))
    social = _compose(chart, Statements(fields=(Statement("supports", stated="  "),)))
    assert social["social_history"]["supports"] == "Sister nearby."


# ---------------------------------------------------------------------------
# Substance use: the eight keys
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("key", SUBSTANCE_KEYS)
@pytest.mark.parametrize(
    ("said", "screen"),
    [
        ((), NOT_ASKED),
        ((Statement("{key}", "asked_no_change"),), ASKED_NO_CHANGE),
        ((Statement("{key}", "denied"),), DENIED),
        (
            (Statement("{key}", "stated", "more than before"),),
            '(stated this visit: "more than before")',
        ),
        (
            (
                Statement("{key}", "asked_no_change"),
                Statement("{key}", "denied"),
                Statement("{key}", "stated", "quit in June"),
            ),
            '(stated this visit: "quit in June")',
        ),
        ((Statement("{key}", "asked_no_change"), Statement("{key}", "denied")), DENIED),
    ],
)
def test_a_substance_field_is_the_baseline_then_one_screen(
    key: str, said: tuple[Statement, ...], screen: str
) -> None:
    field_key = "other_substances" if key == "other" else key
    statements = Statements(fields=tuple(Statement(field_key, s.screen, s.stated) for s in said))
    with_baseline = _compose(ChartContext(history=_history((key, "Weekends only."))), statements)
    without = _compose(ChartContext(), statements)
    assert with_baseline["substance_use"][field_key] == f"Weekends only. {screen}"
    assert without["substance_use"][field_key] == f"Not recorded {screen}"


def test_the_follow_up_prints_all_eight_substance_keys() -> None:
    assert len(SUBSTANCE_KEYS) == 8
    printed = _compose()["substance_use"]
    assert len(printed) == 8
    assert all(v == f"Not recorded {NOT_ASKED}" for v in printed.values())


# ---------------------------------------------------------------------------
# Allergies
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("chart", "printed"),
    [
        (ChartContext(), "Not recorded"),
        (ChartContext(allergy_status="nkda"), "No known drug allergies (NKDA)"),
        (
            ChartContext(
                allergy_status="recorded",
                allergies=(
                    {"substance": "Penicillin", "reaction": "hives"},
                    {"substance": "Sulfa"},
                ),
            ),
            "Penicillin (hives); Sulfa",
        ),
    ],
)
def test_the_allergies_are_the_charts_whatever_was_said(chart: ChartContext, printed: str) -> None:
    assert _compose(chart)["medications"]["allergies"] == printed
    said = Statements(fields=(Statement("allergies", stated="amoxicillin gave me a rash"),))
    assert _compose(chart, said)["medications"]["allergies"] == (
        f'{printed} (stated this visit: "amoxicillin gave me a rash")'
    )


def test_a_denial_on_an_empty_chart_is_quoted_never_nkda() -> None:
    said = Statements(fields=(Statement("allergies", stated="no allergies I know of"),))
    assert _compose(ChartContext(), said)["medications"]["allergies"] == (
        'Not recorded (stated this visit: "no allergies I know of")'
    )


def test_an_entered_value_under_the_fields_own_key_is_the_clinicians_statement() -> None:
    """The evaluation asks for allergies before the visit too."""
    content = _compose(
        ChartContext(),
        inputs={"place_of_service": "In office", "allergies": "NKDA per client"},
        template="psychiatric_evaluation",
    )
    assert content["medications"]["allergies"] == (
        'Not recorded (stated this visit: "NKDA per client")'
    )


# ---------------------------------------------------------------------------
# Current medications
# ---------------------------------------------------------------------------


def test_no_medications_on_the_chart_reads_none_recorded() -> None:
    assert _compose()["medications"]["current_medications"] == ["None recorded"]


def test_uncategorized_medications_are_the_charts_lines() -> None:
    chart = ChartContext(
        medications=(
            ChartMedication("Bupropion XL", "150 mg"),
            ChartMedication("Sertraline", "50 mg", "every morning"),
        )
    )
    assert _compose(chart)["medications"]["current_medications"] == [
        "Bupropion XL 150 mg",
        "Sertraline 50 mg, every morning",
    ]


def test_categorized_medications_name_their_category() -> None:
    chart = ChartContext(
        medications=(
            ChartMedication("Sertraline", "50 mg", "every morning", "psychiatric"),
            ChartMedication("Lisinopril", "10 mg", "daily", "other"),
            ChartMedication("Melatonin", "3 mg"),
        )
    )
    assert _compose(chart)["medications"]["current_medications"] == [
        "Psychiatric: Sertraline 50 mg, every morning",
        "Other: Lisinopril 10 mg, daily",
        "Melatonin 3 mg",
    ]


def test_a_stated_medication_is_its_own_item_and_never_alters_a_chart_line() -> None:
    chart = ChartContext(medications=(ChartMedication("Sertraline", "50 mg", "every morning"),))
    said = Statements(
        fields=(
            Statement("current_medications", stated="omeprazole 20 mg for heartburn"),
            Statement("current_medications", stated="sertraline, I take 100 now"),
            Statement("current_medications", stated="omeprazole 20 mg for heartburn"),
        )
    )
    assert _compose(chart, said)["medications"]["current_medications"] == [
        "Sertraline 50 mg, every morning",
        '(stated this visit: "omeprazole 20 mg for heartburn")',
        '(stated this visit: "sertraline, I take 100 now")',
    ]


@pytest.mark.parametrize(
    ("stated", "restates"),
    [
        ("escitalopram 10 milligrams every morning", True),
        ("I'm still on the escitalopram", True),
        ("Escitalopram, 10", True),
        ("I take 20 of the escitalopram now", False),
        ("omeprazole 20 mg", False),
        ("escitalopram 10 and melatonin 3 at night", False),
    ],
)
def test_a_medication_only_restating_a_chart_line_adds_nothing(stated: str, restates: bool) -> None:
    chart = ChartContext(
        medications=(ChartMedication("Escitalopram", "10 mg", "every morning", "psychiatric"),)
    )
    said = Statements(fields=(Statement("current_medications", stated=stated),))
    current = _compose(chart, said)["medications"]["current_medications"]
    assert current[0] == "Psychiatric: Escitalopram 10 mg, every morning"
    assert current[1:] == ([] if restates else [f'(stated this visit: "{stated}")'])


# ---------------------------------------------------------------------------
# Diagnoses
# ---------------------------------------------------------------------------


CHART_PROBLEMS = ChartContext(
    problems=(
        ChartProblem("Generalized anxiety disorder", "F41.1", "active"),
        ChartProblem("Insomnia", None, "active"),
        ChartProblem("Bipolar II disorder", "F31.81", "rule_out"),
    )
)


def test_the_diagnoses_are_the_problem_list_with_its_codes() -> None:
    assert _compose(CHART_PROBLEMS)["assessment"]["diagnoses"] == [
        {"label": "Generalized anxiety disorder", "code": "F41.1", "status": None},
        {"label": "Insomnia", "code": None, "status": None},
        {"label": "Bipolar II disorder", "code": "F31.81", "status": "rule-out"},
    ]


def test_an_empty_problem_list_with_nothing_named_is_an_empty_list() -> None:
    assert _compose()["assessment"]["diagnoses"] == []


def test_a_diagnosis_named_this_visit_follows_the_list_coded_only_if_said() -> None:
    named = Statements(
        diagnoses=(
            NamedDiagnosis("ADHD, combined presentation", "F90.2"),
            NamedDiagnosis("Major depressive disorder, in remission"),
            NamedDiagnosis("generalized anxiety disorder"),
            NamedDiagnosis("GAD", "f41.1"),
        )
    )
    assert _compose(CHART_PROBLEMS, named)["assessment"]["diagnoses"][3:] == [
        {"label": "ADHD, combined presentation", "code": "F90.2", "status": "stated this visit"},
        {
            "label": "Major depressive disorder, in remission",
            "code": None,
            "status": "stated this visit",
        },
    ]


# ---------------------------------------------------------------------------
# Place of service
# ---------------------------------------------------------------------------


TELEHEALTH = {
    "place_of_service": "Telehealth",
    "client_location": "Ohio",
    "provider_location": "Michigan",
}


@pytest.mark.parametrize(
    ("inputs", "at_home", "person", "printed"),
    [
        (
            TELEHEALTH,
            False,
            "client",
            "Visit conducted by synchronous audio and video telehealth on a HIPAA-compliant "
            "platform. The client was in Ohio; the provider was in Michigan. The client "
            "consented to receive care by telehealth.",
        ),
        (
            TELEHEALTH,
            True,
            "patient",
            "Visit conducted by synchronous audio and video telehealth on a HIPAA-compliant "
            "platform. The patient was at home in Ohio; the provider was in Michigan. The "
            "patient consented to receive care by telehealth.",
        ),
        (
            {"place_of_service": "Telehealth"},
            False,
            "client",
            "Visit conducted by synchronous audio and video telehealth on a HIPAA-compliant "
            "platform. The client's location was not entered; the provider's location was not "
            "entered. The client consented to receive care by telehealth.",
        ),
        (
            {**TELEHEALTH, "client_location": "Home, Columbus, Ohio"},
            True,
            "client",
            "Visit conducted by synchronous audio and video telehealth on a HIPAA-compliant "
            "platform. The client was in Home, Columbus, Ohio; the provider was in Michigan. The "
            "client consented to receive care by telehealth.",
        ),
        ({"place_of_service": "In office"}, True, "client", IN_OFFICE),
        ({}, False, "client", "Not stated."),
        ({"place_of_service": "not provided"}, False, "client", "Not stated."),
    ],
)
def test_the_place_of_service_is_written_from_the_entered_values(
    inputs: dict[str, str], at_home: bool, person: str, printed: str
) -> None:
    text = place_of_service(inputs, person, at_home=at_home)
    assert text == printed
    assert "located at" not in text


def test_the_place_of_service_uses_the_charts_people_term() -> None:
    content = _compose(ChartContext(person="patient"), Statements(client_at_home=True), TELEHEALTH)
    assert "The patient was at home in Ohio" in content["encounter"]["place_of_service"]


# ---------------------------------------------------------------------------
# The extraction call: prompt, schema, evidence
# ---------------------------------------------------------------------------


INDEXED = "\n".join(
    [
        "[S0] [00:01] Therapist: Any alcohol?",
        "[S1] [00:03] Client: Same as always.",
        "[S2] [00:05] Client: I was laid off.",
        "[S3] [00:07] Therapist: Note: generalized anxiety, worse.",
    ]
)
SEGMENTS = {i: line.partition("] ")[2] for i, line in enumerate(INDEXED.splitlines())}


def test_the_extraction_prompt_is_short_and_shows_the_charts_text() -> None:
    fields = rendered_fields(_definition())
    chart = ChartContext(history=_history(("work_school", "Employed.")), person="patient")
    prompt = build_prompt(fields, chart, TELEHEALTH, INDEXED)
    instructions = prompt.partition("\n\nFields, with")[0]
    assert len(instructions.splitlines()) <= 15
    assert "- work_school (Work or school): Employed." in prompt
    assert "- alcohol (Alcohol, substance screen): Not recorded" in prompt
    assert "the patient" in instructions
    assert "client_at_home" in instructions
    assert prompt.endswith(INDEXED)


def test_the_schema_asks_only_for_what_the_type_has() -> None:
    follow_up = rendered_fields(_definition())
    schema = response_schema(follow_up, TELEHEALTH)
    assert schema["title"] == SCHEMA_TITLE
    assert set(schema["properties"]) == {"statements", RISK_LINES, "diagnoses", "client_at_home"}
    keys = schema["properties"]["statements"]["items"]["properties"]["field_key"]["enum"]
    assert "place_of_service" not in keys
    assert "diagnoses" not in keys
    assert "work_school" in keys
    assert "alcohol" in keys

    evaluation = rendered_fields(_definition("psychiatric_evaluation"))
    assert set(response_schema(evaluation, {"place_of_service": "In office"})["properties"]) == {
        "statements",
        RISK_LINES,
    }


def test_an_item_citing_no_line_or_a_line_the_visit_lacks_is_dropped() -> None:
    fields = rendered_fields(_definition())
    reply = {
        "statements": [
            {
                "field_key": "alcohol",
                "screen": "asked_no_change",
                "stated": "",
                "evidence_segment_ids": [0, 1],
            },
            {
                "field_key": "work_school",
                "screen": "stated",
                "stated": "laid off",
                "evidence_segment_ids": [2],
            },
            {
                "field_key": "supports",
                "screen": "stated",
                "stated": "my sister",
                "evidence_segment_ids": [],
            },
            {
                "field_key": "relationships",
                "screen": "stated",
                "stated": "invented",
                "evidence_segment_ids": [9],
            },
            {
                "field_key": "risk",
                "screen": "stated",
                "stated": "not a chart field",
                "evidence_segment_ids": [1],
            },
            {
                "field_key": "cannabis",
                "screen": "maybe",
                "stated": "weekends",
                "evidence_segment_ids": [1],
            },
        ],
        "diagnoses": [
            {"label": "Generalized anxiety disorder", "evidence_segment_ids": [3]},
            {"label": "Bipolar disorder", "code": "F31.9", "evidence_segment_ids": [42]},
        ],
        "client_at_home": {"at_home": True, "evidence_segment_ids": []},
    }
    kept = parse(reply, fields, TELEHEALTH, SEGMENTS)
    assert kept.fields == (
        Statement("alcohol", "asked_no_change", ""),
        Statement("work_school", "stated", "laid off"),
        Statement("cannabis", "stated", "weekends"),
    )
    assert kept.diagnoses == (NamedDiagnosis("Generalized anxiety disorder"),)
    assert kept.client_at_home is False

    home = parse(
        {"client_at_home": {"at_home": True, "evidence_segment_ids": [1]}},
        fields,
        TELEHEALTH,
        SEGMENTS,
    )
    assert home.client_at_home is True
    office = parse(
        {"client_at_home": {"at_home": True, "evidence_segment_ids": [1]}},
        fields,
        {"place_of_service": "In office"},
        SEGMENTS,
    )
    assert office.client_at_home is False


def test_a_history_statement_citing_only_where_the_client_is_today_is_dropped() -> None:
    """A telehealth client's location for the visit is the attestation's, never history."""
    fields = rendered_fields(_definition())
    segments = {
        0: "Therapist: Are you at your apartment?",
        1: "Client: Yep.",
        2: "Client: We moved.",
    }
    reply = {
        "statements": [
            {
                "field_key": "living_situation",
                "screen": "stated",
                "stated": "Yep.",
                "evidence_segment_ids": [0, 1],
            },
            {
                "field_key": "living_situation",
                "screen": "stated",
                "stated": "We moved.",
                "evidence_segment_ids": [1, 2],
            },
            {"field_key": "alcohol", "screen": "denied", "stated": "", "evidence_segment_ids": [1]},
        ],
        "client_at_home": {"at_home": False, "evidence_segment_ids": [0, 1]},
    }
    kept = parse(reply, fields, TELEHEALTH, segments)
    assert kept.fields == (
        Statement("living_situation", "stated", "We moved."),
        Statement("alcohol", "denied", ""),
    )
    assert kept.client_at_home is False
    # In the office nothing is asked about where the client is, so nothing is dropped.
    office = parse(reply, fields, {"place_of_service": "In office"}, segments)
    assert len(office.fields) == 3


def test_a_history_statement_citing_only_the_risk_screen_is_dropped() -> None:
    """A crisis contact named in the safety plan is not a change to the client's supports."""
    fields = rendered_fields(_definition())
    segments = {
        0: "Therapist: Who would you call if it got bad?",
        1: "Client: Call my sister, she's always up late.",
        2: "Client: My sister moved in with me last month.",
    }
    reply = {
        "statements": [
            {
                "field_key": "supports",
                "screen": "stated",
                "stated": "Call my sister",
                "evidence_segment_ids": [1],
            },
            {
                "field_key": "living_situation",
                "screen": "stated",
                "stated": "My sister moved in",
                "evidence_segment_ids": [2],
            },
        ],
        RISK_LINES: [0, 1, 99, True],
    }
    kept = parse(reply, fields, {"place_of_service": "In office"}, segments)
    assert kept.fields == (Statement("living_situation", "stated", "My sister moved in"),)


# ---------------------------------------------------------------------------
# Through the generation service: two calls, the main one shrunk
# ---------------------------------------------------------------------------


@dataclass
class _ScriptedGateway(StructuredLLMGateway):
    """Answers the draft and the extraction by the schema they send; records both."""

    extraction: dict[str, Any] | Exception = field(default_factory=lambda: {"statements": []})
    calls: list[dict[str, Any]] = field(default_factory=list)
    lock: threading.Lock = field(default_factory=threading.Lock)

    def complete_structured(self, **kwargs: Any) -> StructuredCompletion:
        with self.lock:
            self.calls.append(kwargs)
        schema = kwargs["response_schema"]
        if schema.get("title") == SCHEMA_TITLE:
            if isinstance(self.extraction, Exception):
                raise self.extraction
            return StructuredCompletion(data=self.extraction)
        if "runs" in schema.get("properties", {}):
            return StructuredCompletion(data={"runs": []})
        return StructuredCompletion(data=_stand_in(schema))


def _stand_in(schema: dict[str, Any]) -> Any:
    kind = schema.get("type")
    if kind == "object":
        return {k: _stand_in(v) for k, v in schema.get("properties", {}).items()}
    if kind == "array":
        return []
    return "Drafted by the model." if kind == "string" else None


FULL_CHART = ChartContext(
    problems=(ChartProblem("Generalized anxiety disorder", "F41.1", "active"),),
    allergy_status="recorded",
    allergies=({"substance": "Sulfa", "reaction": "rash"},),
    medications=(ChartMedication("Sertraline", "50 mg", "every morning"),),
    history=_history(
        ("work_school", "Teaches third grade at a public school."),
        ("alcohol", "Two glasses of wine on weekends."),
    ),
)
TRANSCRIPT = Transcript(
    format="txt",
    content="[00:01] Therapist: Any alcohol?\n[00:03] Client: Same as always.",
)


def _draft(gateway: _ScriptedGateway, inputs: dict[str, str] | None = None) -> dict[str, Any]:
    definition = _definition()
    service = RegistryNoteGenerationService(llm_gateway=gateway, model="scripted")
    generated = service.generate_note(
        definition.key,
        TRANSCRIPT,
        PATIENT,
        NOW,
        inputs=inputs or {"place_of_service": "In office"},
        definition=definition,
        chart=FULL_CHART,
        client_present_end_seconds=0,
    )
    return generated.content


def _main_call(gateway: _ScriptedGateway) -> dict[str, Any]:
    return next(c for c in gateway.calls if c["response_schema"].get("title") != SCHEMA_TITLE)


def test_the_main_call_no_longer_asks_for_or_sees_the_chart_fed_fields() -> None:
    gateway = _ScriptedGateway()
    _draft(gateway)
    main = _main_call(gateway)
    asked = {
        key
        for section in main["response_schema"]["properties"].values()
        for key in section.get("properties", {})
    }
    rendered = {r.field.key for r in rendered_fields(_definition())}
    assert not asked & rendered
    assert "substance_use" not in main["response_schema"]["properties"]
    assert {"chief_complaint", "formulation", "medication_plan"} <= asked
    prompt = main["user_prompt"]
    for chart_only in ("Teaches third grade", "Two glasses of wine", "Sulfa"):
        assert chart_only not in prompt
    # What the assessment and plan judge against stays.
    assert "F41.1 Generalized anxiety disorder" in prompt
    assert "Sertraline 50 mg, every morning" in prompt
    assert "From the chart, exactly as given" not in prompt


def test_the_chart_fed_fields_are_printed_with_no_model_text_in_them() -> None:
    gateway = _ScriptedGateway(
        extraction={
            "statements": [
                {
                    "field_key": "alcohol",
                    "screen": "asked_no_change",
                    "stated": "",
                    "evidence_segment_ids": [0, 1],
                }
            ],
            "diagnoses": [],
        }
    )
    content = _draft(gateway)
    assert content["medications"]["allergies"] == "Sulfa (rash)"
    assert content["medications"]["current_medications"] == ["Sertraline 50 mg, every morning"]
    assert content["social_history"]["work_school"] == "Teaches third grade at a public school."
    assert content["social_history"]["supports"] == "Not recorded"
    assert (
        content["substance_use"]["alcohol"] == f"Two glasses of wine on weekends. {ASKED_NO_CHANGE}"
    )
    assert content["substance_use"]["cannabis"] == f"Not recorded {NOT_ASKED}"
    assert content["assessment"]["diagnoses"] == [
        {"label": "Generalized anxiety disorder", "code": "F41.1", "status": None}
    ]
    assert content["encounter"]["place_of_service"] == IN_OFFICE
    assert content["subjective"]["chief_complaint"] == "Drafted by the model."
    # Two calls only: the psychotherapy section is dropped with no client present.
    assert len(gateway.calls) == 2


def test_a_failed_extraction_fails_the_draft() -> None:
    gateway = _ScriptedGateway(extraction=ValueError("schema refused"))
    with pytest.raises(ValueError, match="Note generation failed"):
        _draft(gateway)
