# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""The E/M level and code from the clinician's MDM choices, and the add-on band.

The grid below is written out by hand from the two-of-three rule rather than
computed, so a change to the rule has to change the table too.
"""

from __future__ import annotations

import pytest
from app.notes.mdm import (
    DATA_LEVELS,
    PROBLEMS_LEVELS,
    RISK_LEVELS,
    DataLevel,
    ElementRationale,
    ProblemsLevel,
    RiskLevel,
    billing_methods,
    em_code,
    em_level,
    psychotherapy_add_on,
    rationale,
)

# problems, data, risk -> established-patient code, new-patient code
GRID: tuple[tuple[ProblemsLevel, DataLevel, RiskLevel, str, str], ...] = (
    ("minimal", "none", "minimal", "99212", "99202"),
    ("minimal", "none", "low", "99212", "99202"),
    ("minimal", "none", "moderate", "99212", "99202"),
    ("minimal", "none", "high", "99212", "99202"),
    ("minimal", "limited", "minimal", "99212", "99202"),
    ("minimal", "limited", "low", "99213", "99203"),
    ("minimal", "limited", "moderate", "99213", "99203"),
    ("minimal", "limited", "high", "99213", "99203"),
    ("minimal", "moderate", "minimal", "99212", "99202"),
    ("minimal", "moderate", "low", "99213", "99203"),
    ("minimal", "moderate", "moderate", "99214", "99204"),
    ("minimal", "moderate", "high", "99214", "99204"),
    ("minimal", "extensive", "minimal", "99212", "99202"),
    ("minimal", "extensive", "low", "99213", "99203"),
    ("minimal", "extensive", "moderate", "99214", "99204"),
    ("minimal", "extensive", "high", "99215", "99205"),
    ("low", "none", "minimal", "99212", "99202"),
    ("low", "none", "low", "99213", "99203"),
    ("low", "none", "moderate", "99213", "99203"),
    ("low", "none", "high", "99213", "99203"),
    ("low", "limited", "minimal", "99213", "99203"),
    ("low", "limited", "low", "99213", "99203"),
    ("low", "limited", "moderate", "99213", "99203"),
    ("low", "limited", "high", "99213", "99203"),
    ("low", "moderate", "minimal", "99213", "99203"),
    ("low", "moderate", "low", "99213", "99203"),
    ("low", "moderate", "moderate", "99214", "99204"),
    ("low", "moderate", "high", "99214", "99204"),
    ("low", "extensive", "minimal", "99213", "99203"),
    ("low", "extensive", "low", "99213", "99203"),
    ("low", "extensive", "moderate", "99214", "99204"),
    ("low", "extensive", "high", "99215", "99205"),
    ("moderate", "none", "minimal", "99212", "99202"),
    ("moderate", "none", "low", "99213", "99203"),
    ("moderate", "none", "moderate", "99214", "99204"),
    ("moderate", "none", "high", "99214", "99204"),
    ("moderate", "limited", "minimal", "99213", "99203"),
    ("moderate", "limited", "low", "99213", "99203"),
    ("moderate", "limited", "moderate", "99214", "99204"),
    ("moderate", "limited", "high", "99214", "99204"),
    ("moderate", "moderate", "minimal", "99214", "99204"),
    ("moderate", "moderate", "low", "99214", "99204"),
    ("moderate", "moderate", "moderate", "99214", "99204"),
    ("moderate", "moderate", "high", "99214", "99204"),
    ("moderate", "extensive", "minimal", "99214", "99204"),
    ("moderate", "extensive", "low", "99214", "99204"),
    ("moderate", "extensive", "moderate", "99214", "99204"),
    ("moderate", "extensive", "high", "99215", "99205"),
    ("high", "none", "minimal", "99212", "99202"),
    ("high", "none", "low", "99213", "99203"),
    ("high", "none", "moderate", "99214", "99204"),
    ("high", "none", "high", "99215", "99205"),
    ("high", "limited", "minimal", "99213", "99203"),
    ("high", "limited", "low", "99213", "99203"),
    ("high", "limited", "moderate", "99214", "99204"),
    ("high", "limited", "high", "99215", "99205"),
    ("high", "moderate", "minimal", "99214", "99204"),
    ("high", "moderate", "low", "99214", "99204"),
    ("high", "moderate", "moderate", "99214", "99204"),
    ("high", "moderate", "high", "99215", "99205"),
    ("high", "extensive", "minimal", "99215", "99205"),
    ("high", "extensive", "low", "99215", "99205"),
    ("high", "extensive", "moderate", "99215", "99205"),
    ("high", "extensive", "high", "99215", "99205"),
)


def test_the_grid_covers_every_combination_once() -> None:
    combinations = {(p, d, r) for p, d, r, _, _ in GRID}
    assert len(GRID) == len(combinations) == 64
    assert combinations == {
        (p, d, r) for p in PROBLEMS_LEVELS for d in DATA_LEVELS for r in RISK_LEVELS
    }


@pytest.mark.parametrize(("problems", "data", "risk", "established", "new"), GRID)
def test_the_code_follows_two_of_three(
    problems: ProblemsLevel, data: DataLevel, risk: RiskLevel, established: str, new: str
) -> None:
    level = em_level(problems, data, risk)
    assert em_code(level, new_patient=False) == established
    assert em_code(level, new_patient=True) == new


@pytest.mark.parametrize(("problems", "data", "risk", "established", "new"), GRID)
def test_two_elements_always_meet_the_level(
    problems: ProblemsLevel, data: DataLevel, risk: RiskLevel, established: str, new: str
) -> None:
    assert sum(e.meets for e in rationale(problems, data, risk)) >= 2


def test_the_everyday_medication_check_turns_on_problems() -> None:
    # Managing a prescription is moderate risk; one stable illness is low problems.
    assert em_code(em_level("low", "limited", "moderate"), new_patient=False) == "99213"
    assert em_code(em_level("moderate", "limited", "moderate"), new_patient=False) == "99214"


@pytest.mark.parametrize(
    ("minutes", "code"),
    [
        (None, None),
        (0, None),
        (15, None),
        (16, "90833"),
        (37, "90833"),
        (38, "90836"),
        (52, "90836"),
        (53, "90838"),
        (90, "90838"),
    ],
)
def test_the_add_on_follows_psychotherapy_minutes(minutes: int | None, code: str | None) -> None:
    assert psychotherapy_add_on(minutes) == code


def test_time_is_not_offered_once_psychotherapy_is_billed() -> None:
    assert billing_methods(has_psychotherapy=True) == ("mdm",)


def test_time_is_offered_for_a_visit_without_psychotherapy() -> None:
    assert billing_methods(has_psychotherapy=False) == ("mdm", "time")


def test_the_rationale_names_what_each_element_needed() -> None:
    assert rationale("moderate", "limited", "moderate") == (
        ElementRationale("problems", "moderate", "moderate", meets=True),
        ElementRationale("data", "limited", "moderate", meets=False),
        ElementRationale("risk", "moderate", "moderate", meets=True),
    )


def test_the_rationale_for_a_straightforward_visit() -> None:
    assert rationale("low", "none", "minimal") == (
        ElementRationale("problems", "low", "minimal", meets=True),
        ElementRationale("data", "none", "none", meets=True),
        ElementRationale("risk", "minimal", "minimal", meets=True),
    )
