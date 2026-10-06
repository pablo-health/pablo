# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Scoring in the local derive runner, on synthetic note types."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from app.notes.practice_types import PracticeNoteTypeSpec, to_definition
from evals.run_derive import (
    default_inputs,
    main,
    match_labels,
    order_agreement,
    score_structure,
    similarity,
)

if TYPE_CHECKING:
    from pathlib import Path

    import pytest


def _spec(sections: dict[str, list[str]], **extra: Any) -> PracticeNoteTypeSpec:
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
            **extra,
        }
    )


REFERENCE = _spec(
    {
        "Interval history": ["Interval history", "Side effects"],
        "Mental status exam": ["Appearance", "Mood and affect"],
        "Assessment": ["Assessment"],
        "Plan": ["Medication changes", "Follow-up"],
    }
)


def test_labels_match_on_shared_words_not_spelling() -> None:
    assert similarity("Current medications", "Medication list") == 0.5
    assert similarity("Mental status exam", "Mental Status Examination") == 1.0
    assert similarity("Plan", "Assessment") == 0.0
    assert similarity("and the", "Plan") == 0.0


def test_each_label_matches_at_most_once_best_first() -> None:
    pairs = match_labels(["Plan", "Plan and follow-up"], ["Plan and follow-up", "Plan"])

    assert pairs == [(1, 0, 1.0), (0, 1, 1.0)]


def test_order_agreement_is_the_longest_run_in_order() -> None:
    assert order_agreement([(0, 0, 1.0), (1, 1, 1.0), (2, 2, 1.0)]) == 1.0
    assert order_agreement([(2, 0, 1.0), (0, 1, 1.0), (1, 2, 1.0)]) == 0.67
    assert order_agreement([]) == 1.0


def test_a_faithful_proposal_scores_full_marks() -> None:
    score = score_structure(REFERENCE, REFERENCE)

    assert (score.section_recall, score.section_precision, score.section_order) == (1, 1, 1)
    assert (score.field_recall, score.field_precision) == (1, 1)
    assert score.missing_sections == score.extra_sections == []


def test_missing_extra_and_reordered_parts_are_named() -> None:
    proposed = _spec(
        {
            "Plan": ["Medication changes", "Follow up"],
            "Interval History": ["Interval history"],
            "Billing": ["Visit code"],
        }
    )

    score = score_structure(proposed, REFERENCE)

    assert score.missing_sections == ["Mental status exam", "Assessment"]
    assert score.extra_sections == ["Billing"]
    assert score.section_recall == 0.5
    assert score.section_order == 0.5
    assert "Interval history / Side effects" in score.missing_fields
    assert score.extra_fields == ["Billing / Visit code"]


def test_required_inputs_get_a_placeholder_for_drafting() -> None:
    spec = _spec(
        {"Plan": ["Plan"]},
        inputs=[
            {
                "key": "where",
                "label": "Where",
                "kind": "choice",
                "options": ["Office", "Video"],
                "required": True,
            },
            {"key": "why", "label": "Why", "required": True},
            {"key": "note", "label": "Note"},
        ],
    )

    filled = default_inputs(to_definition("custom.x", 0, spec), {"why": "Refill"})

    assert filled == {"where": "Office", "why": "Refill"}


def test_the_runner_writes_only_to_its_output_directory(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """End to end on the stand-in; the console never carries sample text."""
    sample = tmp_path / "in" / "note.txt"
    sample.parent.mkdir()
    stray = "The client brought a drawing from a weekend art class to show."
    sample.write_text(
        "Interval history: Sleeping better since the last visit, appetite steady.\n"
        f"Follow up: Return in four weeks for a medication check.\n{stray}\n"
    )
    reference = tmp_path / "in" / "reference.json"
    reference.write_text(REFERENCE.model_dump_json())
    out = tmp_path / "out"

    assert (
        main(
            [
                "--stand-in",
                "--samples",
                str(sample),
                "--reference-spec",
                str(reference),
                "--out",
                str(out),
            ]
        )
        == 0
    )

    assert sorted(p.name for p in out.iterdir()) == ["proposal.json", "report.json", "report.md"]
    report = json.loads((out / "report.json").read_text())
    assert report["coverage"][0]["unplaced"] == [stray]
    assert report["copied_in_final"] == []
    assert "drawing" not in capsys.readouterr().out
    assert sorted(p.name for p in sample.parent.iterdir()) == ["note.txt", "reference.json"]
