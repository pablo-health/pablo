# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Medical decision making: the E/M level and code, and the psychotherapy add-on.

An office or outpatient E/M visit is levelled by three elements of medical
decision making (MDM):

- the number and complexity of **problems** addressed at the visit;
- the amount and complexity of **data** reviewed and analyzed;
- the **risk** of complications, morbidity or mortality of patient management.

The visit's MDM level is the highest level that at least two of the three
elements meet. When fewer than two elements reach low, the level is
straightforward. Each level maps to one code: 99202-99205 for a new patient,
99212-99215 for an established one.

A psychotherapy add-on is coded on psychotherapy minutes alone: 16-37 minutes
is 90833, 38-52 is 90836, 53 or more is 90838, and under 16 minutes there is
no add-on. When an add-on is billed, the E/M level is chosen by MDM, never by
time; time-based E/M selection exists only for a visit without one.

The clinician chooses each element's level. Nothing here suggests one, and
nothing here reads a transcript: these are the rules the chosen levels feed.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, get_args

ProblemsLevel = Literal["minimal", "low", "moderate", "high"]
DataLevel = Literal["none", "limited", "moderate", "extensive"]
RiskLevel = Literal["minimal", "low", "moderate", "high"]
MdmLevel = Literal["straightforward", "low", "moderate", "high"]
MdmElement = Literal["problems", "data", "risk"]
BillingMethod = Literal["mdm", "time"]

PROBLEMS_LEVELS: tuple[ProblemsLevel, ...] = get_args(ProblemsLevel)
DATA_LEVELS: tuple[DataLevel, ...] = get_args(DataLevel)
RISK_LEVELS: tuple[RiskLevel, ...] = get_args(RiskLevel)
MDM_LEVELS: tuple[MdmLevel, ...] = get_args(MdmLevel)
"""Each tuple runs lowest to highest; the same position is the same MDM level."""

_NEW_PATIENT_CODES: dict[MdmLevel, str] = {
    "straightforward": "99202",
    "low": "99203",
    "moderate": "99204",
    "high": "99205",
}
_ESTABLISHED_PATIENT_CODES: dict[MdmLevel, str] = {
    "straightforward": "99212",
    "low": "99213",
    "moderate": "99214",
    "high": "99215",
}

_ADD_ON_BANDS: tuple[tuple[int, str], ...] = ((53, "90838"), (38, "90836"), (16, "90833"))
"""Fewest psychotherapy minutes for each add-on, longest band first."""


@dataclass(frozen=True)
class ElementRationale:
    """One element's part in the level: what was chosen, and what the level needed."""

    element: MdmElement
    chosen: str
    required: str
    meets: bool


def _ranks(problems: ProblemsLevel, data: DataLevel, risk: RiskLevel) -> tuple[int, int, int]:
    return (PROBLEMS_LEVELS.index(problems), DATA_LEVELS.index(data), RISK_LEVELS.index(risk))


def em_level(problems: ProblemsLevel, data: DataLevel, risk: RiskLevel) -> MdmLevel:
    """The MDM level: the highest level at least two of the three elements meet."""
    # The middle of three ranks is the highest one that two of them reach.
    middle = sorted(_ranks(problems, data, risk))[1]
    return MDM_LEVELS[middle]


def em_code(level: MdmLevel, *, new_patient: bool) -> str:
    """The office or outpatient E/M code for an MDM level."""
    return (_NEW_PATIENT_CODES if new_patient else _ESTABLISHED_PATIENT_CODES)[level]


def psychotherapy_add_on(minutes: int | None) -> str | None:
    """The add-on code for this many psychotherapy minutes; ``None`` under 16."""
    if minutes is None:
        return None
    return next((code for floor, code in _ADD_ON_BANDS if minutes >= floor), None)


def billing_methods(*, has_psychotherapy: bool) -> tuple[BillingMethod, ...]:
    """How the E/M level may be chosen: by MDM only once an add-on is billed."""
    return ("mdm",) if has_psychotherapy else ("mdm", "time")


def rationale(
    problems: ProblemsLevel, data: DataLevel, risk: RiskLevel
) -> tuple[ElementRationale, ...]:
    """Per element, the level chosen and the level the computed MDM level required."""
    level_rank = MDM_LEVELS.index(em_level(problems, data, risk))
    chosen = (problems, data, risk)
    ladders: tuple[tuple[str, ...], ...] = (PROBLEMS_LEVELS, DATA_LEVELS, RISK_LEVELS)
    elements: tuple[MdmElement, ...] = get_args(MdmElement)
    return tuple(
        ElementRationale(
            element=element,
            chosen=picked,
            required=ladder[level_rank],
            meets=ladder.index(picked) >= level_rank,
        )
        for element, picked, ladder in zip(elements, chosen, ladders, strict=True)
    )


__all__ = [
    "DATA_LEVELS",
    "MDM_LEVELS",
    "PROBLEMS_LEVELS",
    "RISK_LEVELS",
    "BillingMethod",
    "DataLevel",
    "ElementRationale",
    "MdmElement",
    "MdmLevel",
    "ProblemsLevel",
    "RiskLevel",
    "billing_methods",
    "em_code",
    "em_level",
    "psychotherapy_add_on",
    "rationale",
]
