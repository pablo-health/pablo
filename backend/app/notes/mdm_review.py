# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""The medical decision making review beside a prescriber's note.

A note type offers the review by declaring the three MDM choices as inputs
(:data:`CHOICE_INPUTS`). The clinician picks each element's level; the model
only drafts one sentence of evidence per element, in a section kept out of
the note (:attr:`~app.notes.registry.NoteSectionDef.review_only`). From the
choices and the confirmed psychotherapy minutes, :mod:`app.notes.mdm` gives
the level, the E/M code and the add-on. The note itself states only the
codes, in its visit details.

A code the clinician dictated stays as dictated. Where it disagrees with the
computed one, the review says so and offers the text with the computed codes
in place; it never changes the note by itself.
"""

from __future__ import annotations

import copy
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from .mdm import (
    DATA_LEVELS,
    PROBLEMS_LEVELS,
    RISK_LEVELS,
    BillingMethod,
    DataLevel,
    ElementRationale,
    MdmElement,
    MdmLevel,
    ProblemsLevel,
    RiskLevel,
    billing_methods,
    em_code,
    em_level,
    psychotherapy_add_on,
    rationale,
)
from .visit_times import PSYCHOTHERAPY_SECTION_KEY

if TYPE_CHECKING:
    from collections.abc import Mapping

    from .registry import NoteTypeDefinition

ENCOUNTER_SECTION_KEY = "encounter"
VISIT_DETAILS_FIELD = "visit_details"
"""Where the note states its codes."""

MDM_SECTION_KEY = "mdm"
"""The review-only section holding the model's evidence sentences."""

EVIDENCE_FIELDS: dict[MdmElement, str] = {
    "problems": "problems_addressed",
    "data": "data_reviewed",
    "risk": "management_risk",
}
CHOICE_INPUTS: dict[MdmElement, str] = {
    "problems": "mdm_problems",
    "data": "mdm_data",
    "risk": "mdm_risk",
}
NEW_PATIENT_INPUT = "new_patient"
NEW_PATIENT = "new"
REVIEW_INPUTS = (*CHOICE_INPUTS.values(), NEW_PATIENT_INPUT)
"""Inputs the review sets. None of them reaches the model, so none needs a redraft."""

RATIONALE_ORDER: tuple[MdmElement, ...] = ("problems", "risk", "data")
"""Data is usually limited at a medication visit, so problems and risk decide the level."""

_EM_CODE = re.compile(r"\b99(?:20[2-5]|21[2-5])\b")
_ADD_ON_CODE = re.compile(r"\b9083[368]\b")
_EM_UNSTATED = re.compile(r"(E/M[^:\n]*:\s*)not stated\.?", re.IGNORECASE)
_ADD_ON_UNSTATED = re.compile(r"(add-on[^:\n]*:\s*)not stated\.?", re.IGNORECASE)
_NO_ADD_ON = "none"


@dataclass(frozen=True)
class MdmChoices:
    problems: ProblemsLevel | None
    data: DataLevel | None
    risk: RiskLevel | None
    new_patient: bool


@dataclass(frozen=True)
class MdmReview:
    choices: MdmChoices
    evidence: dict[MdmElement, str]
    level: MdmLevel | None
    em_code: str | None
    has_psychotherapy: bool
    psychotherapy_minutes: int | None
    add_on: str | None
    add_on_known: bool
    """False while a psychotherapy portion has no confirmed minutes yet."""
    billing_methods: tuple[BillingMethod, ...]
    rationale: tuple[ElementRationale, ...]
    dictated_em_code: str | None
    dictated_add_on: str | None
    visit_details_with_codes: str | None
    """The visit details with the computed codes in place; ``None`` when that changes nothing."""

    @property
    def em_disagrees(self) -> bool:
        return self.em_code is not None and self.dictated_em_code not in (None, self.em_code)

    @property
    def add_on_disagrees(self) -> bool:
        return self.add_on_known and self.dictated_add_on not in (None, self.add_on)


def offers_review(definition: NoteTypeDefinition) -> bool:
    declared = {i.key for i in definition.inputs}
    return all(key in declared for key in CHOICE_INPUTS.values())


def _level[L: str](value: str | None, levels: tuple[L, ...]) -> L | None:
    return next((level for level in levels if level == value), None)


def choices_from(definition: NoteTypeDefinition, inputs: Mapping[str, str]) -> MdmChoices:
    """The clinician's choices; the patient status falls back to the type's default."""
    status_input = next((i for i in definition.inputs if i.key == NEW_PATIENT_INPUT), None)
    status = inputs.get(NEW_PATIENT_INPUT) or (status_input.default if status_input else None)
    return MdmChoices(
        problems=_level(inputs.get(CHOICE_INPUTS["problems"]), PROBLEMS_LEVELS),
        data=_level(inputs.get(CHOICE_INPUTS["data"]), DATA_LEVELS),
        risk=_level(inputs.get(CHOICE_INPUTS["risk"]), RISK_LEVELS),
        new_patient=status == NEW_PATIENT,
    )


def has_psychotherapy(content: Mapping[str, Any] | None) -> bool:
    """Whether the note has a psychotherapy portion: any of its fields written."""
    section = (content or {}).get(PSYCHOTHERAPY_SECTION_KEY)
    if not isinstance(section, dict):
        return False
    return any(_written(value) for value in section.values())


def _written(value: Any) -> bool:
    if isinstance(value, list):
        return any(str(item).strip() for item in value)
    return bool(str(value or "").strip())


def dictated_codes(visit_details: str) -> tuple[str | None, str | None]:
    """The E/M code and the add-on code the visit details state, if any."""
    em = _EM_CODE.search(visit_details)
    add_on = _ADD_ON_CODE.search(visit_details)
    return (em.group(0) if em else None, add_on.group(0) if add_on else None)


def _with_code(
    text: str, code: str | None, found: re.Pattern[str], unstated: re.Pattern[str], line: str
) -> str:
    if found.search(text):
        return found.sub(code or _NO_ADD_ON, text, count=1)
    if code is None:
        return text
    if unstated.search(text):
        return unstated.sub(lambda m: f"{m.group(1)}{code}.", text, count=1)
    return f"{text.rstrip()}\n{line}: {code}".lstrip()


def with_codes(
    visit_details: str, em: str | None, add_on: str | None, *, add_on_known: bool
) -> str:
    """``visit_details`` with ``em`` and ``add_on`` in place of what it states.

    A stated code is replaced where it stands, and an add-on that does not
    apply reads "none". Where a code is "Not stated." it is filled in, and
    otherwise it gets a line of its own.
    """
    text = visit_details
    if em is not None:
        text = _with_code(text, em, _EM_CODE, _EM_UNSTATED, "E/M code")
    if add_on_known:
        text = _with_code(text, add_on, _ADD_ON_CODE, _ADD_ON_UNSTATED, "Psychotherapy add-on code")
    return text


def with_visit_details(content: Mapping[str, Any] | None, visit_details: str) -> dict[str, Any]:
    """``content`` with its visit details replaced; every other value as it was."""
    updated = copy.deepcopy(dict(content or {}))
    encounter = updated.get(ENCOUNTER_SECTION_KEY)
    updated[ENCOUNTER_SECTION_KEY] = {
        **(encounter if isinstance(encounter, dict) else {}),
        VISIT_DETAILS_FIELD: visit_details,
    }
    return updated


def _text(content: Mapping[str, Any] | None, section: str, key: str) -> str:
    value = ((content or {}).get(section) or {}).get(key)
    return value.strip() if isinstance(value, str) else ""


def review(
    definition: NoteTypeDefinition,
    inputs: Mapping[str, str],
    content: Mapping[str, Any] | None,
    psychotherapy_minutes: int | None,
) -> MdmReview:
    """The review of a note's MDM: what was chosen, what it gives, what the note says."""
    choices = choices_from(definition, inputs)
    problems, data, risk = choices.problems, choices.data, choices.risk
    level: MdmLevel | None = None
    by_element: dict[MdmElement, ElementRationale] = {}
    if problems and data and risk:
        level = em_level(problems, data, risk)
        by_element = {r.element: r for r in rationale(problems, data, risk)}
    code = em_code(level, new_patient=choices.new_patient) if level else None
    therapy = has_psychotherapy(content)
    add_on_known = not therapy or psychotherapy_minutes is not None
    add_on = psychotherapy_add_on(psychotherapy_minutes) if therapy else None
    details = _text(content, ENCOUNTER_SECTION_KEY, VISIT_DETAILS_FIELD)
    dictated_em, dictated_add_on = dictated_codes(details)
    applied = with_codes(details, code, add_on, add_on_known=add_on_known)
    return MdmReview(
        choices=choices,
        evidence={e: _text(content, MDM_SECTION_KEY, key) for e, key in EVIDENCE_FIELDS.items()},
        level=level,
        em_code=code,
        has_psychotherapy=therapy,
        psychotherapy_minutes=psychotherapy_minutes if therapy else None,
        add_on=add_on,
        add_on_known=add_on_known,
        billing_methods=billing_methods(has_psychotherapy=therapy),
        rationale=tuple(by_element[e] for e in RATIONALE_ORDER if e in by_element),
        dictated_em_code=dictated_em,
        dictated_add_on=dictated_add_on,
        visit_details_with_codes=applied if applied != details else None,
    )


def confirmed_minutes(psychotherapy_window: Mapping[str, Any] | None) -> int | None:
    """The psychotherapy minutes the clinician confirmed on the visit, if any."""
    confirmed = (psychotherapy_window or {}).get("confirmed") or {}
    minutes = confirmed.get("minutes")
    return minutes if isinstance(minutes, int) else None


__all__ = [
    "CHOICE_INPUTS",
    "EVIDENCE_FIELDS",
    "MDM_SECTION_KEY",
    "NEW_PATIENT_INPUT",
    "RATIONALE_ORDER",
    "REVIEW_INPUTS",
    "MdmChoices",
    "MdmReview",
    "choices_from",
    "confirmed_minutes",
    "dictated_codes",
    "has_psychotherapy",
    "offers_review",
    "review",
    "with_codes",
    "with_visit_details",
]
