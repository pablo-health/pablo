# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Meaning-based structure scoring in the local derive runner, with a fake judge."""

from __future__ import annotations

from app.notes.practice_types import PracticeNoteTypeSpec
from app.services.structured_llm_gateway import FakeStructuredLLMGateway, StructuredCompletion
from evals.derive_meaning import (
    FieldRef,
    Judge,
    field_refs,
    judge_prompt,
    model_judge,
    parse_judgement,
    score_meaning,
)
from evals.run_derive import score_structure


def _spec(sections: dict[str, list[str]]) -> PracticeNoteTypeSpec:
    return PracticeNoteTypeSpec.model_validate(
        {
            "label": "Visit",
            "sections": [
                {
                    "key": f"s{n}",
                    "label": label,
                    "fields": [{"key": f"f{m}", "label": f} for m, f in enumerate(fields)],
                }
                for n, (label, fields) in enumerate(sections.items())
            ],
        }
    )


REFERENCE = _spec(
    {
        "Mental status": ["Appearance", "Mood and affect"],
        "Risk": ["Suicidal ideation"],
        "Plan": ["Follow-up"],
    }
)
# The same content, regrouped and renamed: nothing shares a label.
REGROUPED = _spec(
    {
        "Objective": ["Presentation", "Emotional state", "Safety screen"],
        "Next steps": ["Return visit"],
        "Billing": ["Visit code"],
    }
)


def _judge_by(table: dict[str, list[str]]) -> Judge:
    def judge(reference: list[FieldRef], proposed: list[FieldRef]) -> dict[int, list[int]]:
        index = {f.label: n for n, f in enumerate(proposed)}
        return {
            n: [index[label] for label in table.get(f.label, [])] for n, f in enumerate(reference)
        }

    return judge


def test_regrouped_content_scores_by_meaning_where_labels_miss_it() -> None:
    judge = _judge_by(
        {
            "Appearance": ["Presentation"],
            "Mood and affect": ["Emotional state"],
            "Suicidal ideation": ["Safety screen"],
            "Follow-up": ["Return visit"],
        }
    )

    by_label = score_structure(REGROUPED, REFERENCE)
    by_meaning = score_meaning(REGROUPED, REFERENCE, judge)

    assert by_label.field_recall == 0.0
    assert by_meaning.field_recall == 1.0
    assert by_meaning.section_recall == 1.0
    assert by_meaning.field_precision == 0.8
    assert by_meaning.unused_fields == ["Billing / Visit code"]
    assert by_meaning.matches[0] == {
        "reference": "Mental status / Appearance",
        "proposed": ["Objective / Presentation"],
    }


def test_a_reference_field_with_no_home_is_missing() -> None:
    judge = _judge_by({"Follow-up": ["Return visit"]})

    score = score_meaning(REGROUPED, REFERENCE, judge)

    assert score.field_recall == 0.25
    assert score.section_recall == 0.33
    assert score.missing_fields == [
        "Mental status / Appearance",
        "Mental status / Mood and affect",
        "Risk / Suicidal ideation",
    ]


def test_out_of_range_numbers_from_the_judge_are_ignored() -> None:
    def judge(reference: list[FieldRef], proposed: list[FieldRef]) -> dict[int, list[int]]:
        return {0: [0, 99], 42: [1]}

    score = score_meaning(REGROUPED, REFERENCE, judge)

    assert score.matches == [
        {"reference": "Mental status / Appearance", "proposed": ["Objective / Presentation"]}
    ]


def test_the_model_judge_numbers_fields_and_reads_its_reply() -> None:
    gateway = FakeStructuredLLMGateway(
        default_response=StructuredCompletion(
            data={"matches": [{"reference": 0, "proposed": [0]}, {"reference": "x"}]}
        )
    )

    mapping = model_judge(gateway, model="m")(field_refs(REFERENCE), field_refs(REGROUPED))

    assert mapping == {0: [0]}
    prompt = gateway.calls[0]["user_prompt"]
    assert "R0: Mental status / Appearance" in prompt
    assert "P4: Billing / Visit code" in prompt


def test_parse_judgement_merges_repeated_entries() -> None:
    data = {"matches": [{"reference": 1, "proposed": [2]}, {"reference": 1, "proposed": [3]}]}

    assert parse_judgement(data) == {1: [2, 3]}
    assert "PROPOSED" in judge_prompt([], [])
