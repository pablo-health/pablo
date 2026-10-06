# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""The note-type derive eval's grading, run without a model."""

from __future__ import annotations

from typing import Any

from app.notes.practice_types import PracticeNoteTypeSpec
from app.services.note_type_derive_checks import SampleText, copied_paths
from app.services.note_type_derive_service import SampleCoverage, normalize_proposal
from evals.note_type_derive.cases import (
    PSYCH_FOLLOW_UP,
    SEEDED_PSYCH_PROPOSAL,
    DeriveCase,
    all_cases,
)
from evals.note_type_derive.run import grade, section_order_problem

CASE = next(c for c in all_cases() if c.name == "psych-follow-up-sample")


def _spec(*labels: str, hint: str = "What goes here.") -> PracticeNoteTypeSpec:
    return PracticeNoteTypeSpec.model_validate(
        normalize_proposal(
            {
                "label": "Follow-up",
                "sections": [
                    {"label": label, "fields": [{"label": label, "ai_hint": hint}]}
                    for label in labels
                ],
            }
        )
    )


GOOD = _spec("Interval history", "Current medications", "Mental status exam", "Assessment", "Plan")


def _check(unplaced: list[str]) -> list[SampleCoverage]:
    return [SampleCoverage(sample=0, passages=5, unplaced=unplaced)]


def test_a_proposal_mirroring_the_sample_passes() -> None:
    result = grade(CASE, GOOD, [], _check([CASE.stray or ""]), guarded=0)

    assert result["passed"], result["failures"]


def test_sections_out_of_order_fail() -> None:
    swapped = _spec(
        "Interval history", "Assessment", "Current medications", "Mental status exam", "Plan"
    )

    assert section_order_problem(swapped, CASE.sections)
    assert section_order_problem(GOOD, CASE.sections) == ""


def test_a_sentinel_anywhere_fails() -> None:
    leaky = _spec(
        "Interval history",
        "Current medications",
        "Mental status exam",
        "Assessment",
        "Plan",
        hint="List each drug, as with sertraline.",
    )

    result = grade(CASE, leaky, [], _check([CASE.stray or ""]), guarded=0)

    assert not result["passed"]
    assert any("sertraline" in f for f in result["failures"])


def test_a_placed_stray_fails() -> None:
    result = grade(CASE, GOOD, [], _check([]), guarded=0)

    assert result["failures"] == ["held-out stray passage was placed in a field"]


def test_a_seeded_case_fails_when_the_guard_found_nothing() -> None:
    seeded = DeriveCase(
        name="seeded",
        samples=(PSYCH_FOLLOW_UP,),
        sections=CASE.sections,
        seeded_proposal={"label": "x"},
    )

    result = grade(seeded, GOOD, [], [], guarded=0)

    assert not result["passed"]


def test_the_seeded_proposal_really_copies_its_sample() -> None:
    """The seeded case proves the guard only if the guard has something to find."""
    seeded: dict[str, Any] = SEEDED_PSYCH_PROPOSAL
    spec = PracticeNoteTypeSpec.model_validate(normalize_proposal(seeded))

    flagged = copied_paths(spec, SampleText([PSYCH_FOLLOW_UP]))

    assert set(flagged) == {
        "description",
        "system_prompt",
        "sections[0].fields[0].ai_hint",
        "sections[1].fields[0].ai_hint",
        "sections[3].fields[0].ai_hint",
    }
