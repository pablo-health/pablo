# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""The starting-template eval's checks, run on hand-made drafts without a model."""

from __future__ import annotations

import copy
from datetime import date
from typing import Any

import pytest
from app.chart_history.fields import HISTORY_GROUPS, SUBSTANCE_USE
from app.notes.practice_types import practice_key, to_definition, validate_note_inputs
from evals.note_templates.cases import (
    ALL_CASES,
    FOLLOW_UP_MEDICATION_ONLY,
    FOLLOW_UP_WITH_THERAPY,
)
from evals.note_templates.scorers import calendar_dates, grade

THERAPY_DRAFT: dict[str, dict[str, Any]] = {
    "encounter": {
        "visit_details": (
            "Date of service: 2026-03-12. E/M code: 99214. Psychotherapy add-on code: 90836."
        ),
        "place_of_service": (
            "Visit conducted by synchronous audio and video telehealth on a HIPAA-compliant "
            "platform. The client was located at Client\u2019s home in Faketown, AA; the provider "
            "was located at Clinic office at 123 Test St, Faketown, AA. The client consented "
            "to receive care by telehealth."
        ),
    },
    "subjective": {"chief_complaint": '"The worry has been bad."'},
    "substance_use": {
        "alcohol": "One to two drinks on weekends. (asked this visit: no change)",
        "tobacco_nicotine": 'Not recorded (stated this visit: "denies.")',
        "cannabis": 'Not recorded (stated this visit: "denies.")',
        "other_substances": 'Not recorded (stated this visit: "denies.")',
    },
    "risk": {
        "suicidal_homicidal_ideation": (
            'Client: \u201cNo. Nothing like that.\u201d Clinician: "Denies SI and HI."'
        ),
        "self_harm_violence": 'Client: "No. Nothing like that."',
        "risk_protective_factors": "Employed, supportive partner, engaged in treatment.",
        "overall_risk": '"Overall acute risk is low."',
        "safety_plan": "",
    },
    "measures": {"measures_reviewed": "GAD-7 completed on Friday: 13, up from 8."},
    "medications": {
        "current_medications": [
            "Psychiatric:",
            "Adderall XR 20 mg, every morning",
            "Sertraline 50 mg, every morning",
        ],
    },
    "assessment": {
        "diagnoses": [
            "F41.1 Generalized anxiety disorder",
            "F90.0 Attention-deficit hyperactivity disorder, predominantly inattentive type",
        ],
    },
    "plan": {
        "pdmp": (
            "State prescription monitoring program (PDMP) reviewed on 2026-03-12: "
            "no early fills, no other prescribers."
        ),
    },
    "psychotherapy": {
        "psychotherapy_time": "10:14 to 10:55, 41 minutes.",
        "issues_addressed": "Night-time worry about work performance.",
        "modality_interventions": "CBT: thought record, worry window.",
        "response": "Engaged; belief fell from 90 to 40.",
    },
}


def _history(recorded: dict[str, str]) -> dict[str, dict[str, str]]:
    """Every chart-history section, each field the chart's text or "Not recorded"."""
    return {
        g.key: {f.key: recorded.get(f.key, "Not recorded") for f in g.fields}
        for g in HISTORY_GROUPS
        if g.key != SUBSTANCE_USE
    }


THERAPY_DRAFT.update(
    _history(
        {
            "prior_diagnoses": (
                "ADHD, predominantly inattentive, diagnosed in college; GAD diagnosed 2024."
            ),
            "work_school": "Financial analyst, full time, since 2022.",
            "family_psychiatric": "Mother: generalized anxiety.\nFather: none known.",
        }
    )
)

MEDICATION_ONLY_DRAFT: dict[str, dict[str, Any]] = {
    "encounter": {
        "visit_details": "Date of service: 2026-03-13. E/M code: Not stated.",
        "place_of_service": "In-office visit.",
    },
    "substance_use": {
        "alcohol": 'Not recorded (stated this visit: "No, I don\'t drink")',
        "tobacco_nicotine": "Not recorded (not asked this visit)",
        "cannabis": "Not recorded (not asked this visit)",
        "other_substances": "Not recorded (not asked this visit)",
    },
    "risk": {
        "suicidal_homicidal_ideation": 'Client: "No." Clinician: "denies SI and HI".',
        "self_harm_violence": "Not stated.",
        "risk_protective_factors": "",
        "overall_risk": "Not stated.",
        "safety_plan": "",
    },
    "measures": {"measures_reviewed": ""},
    "medications": {"current_medications": ["Bupropion XL 150 mg, every morning"]},
    "assessment": {"diagnoses": ["Depression, in remission"]},
    "plan": {"pdmp": ""},
    "psychotherapy": {
        "psychotherapy_time": "",
        "issues_addressed": "",
        "modality_interventions": "",
    },
    **_history({}),
}


def _with(draft: dict[str, dict[str, Any]], section: str, key: str, value: Any) -> Any:
    changed = copy.deepcopy(draft)
    changed.setdefault(section, {})[key] = value
    return changed


def _failed(draft: dict[str, dict[str, Any]], case: Any) -> dict[str, list[str]]:
    return {name: found for name, found in grade(draft, case).items() if found}


def test_good_therapy_draft_passes() -> None:
    assert _failed(THERAPY_DRAFT, FOLLOW_UP_WITH_THERAPY) == {}


def test_good_medication_only_draft_passes() -> None:
    assert _failed(MEDICATION_ONLY_DRAFT, FOLLOW_UP_MEDICATION_ONLY) == {}


def test_what_the_visit_changed_may_follow_a_history_fields_chart_text() -> None:
    draft = _with(
        THERAPY_DRAFT,
        "social_history",
        "work_school",
        'Financial analyst, full time, since 2022. (stated this visit: "on leave this month.")',
    )
    draft = _with(
        draft, "trauma_history", "trauma_history", 'Not recorded (stated this visit: "denies.")'
    )
    assert _failed(draft, FOLLOW_UP_WITH_THERAPY) == {}


def test_a_medication_the_client_reports_may_follow_the_charts_list() -> None:
    draft = _with(
        MEDICATION_ONLY_DRAFT,
        "medications",
        "current_medications",
        ["Bupropion XL 150 mg, every morning", '(stated this visit: "melatonin 3 mg")'],
    )
    assert _failed(draft, FOLLOW_UP_MEDICATION_ONLY) == {}


@pytest.mark.parametrize(
    ("section", "key", "value", "check"),
    [
        # A code or time that was not dictated, anywhere in the note.
        (
            "encounter",
            "visit_details",
            "99215 + 90836, 10:14-10:55, 41 minutes",
            "codes_only_dictated",
        ),
        ("plan", "follow_up", "Return 2026-04-09 at 10:00.", "codes_only_dictated"),
        # A dictated code or time left out.
        ("encounter", "visit_details", "E/M 99214.", "codes_only_dictated"),
        # The visit details restate the psychotherapy time, even as dictated.
        (
            "encounter",
            "visit_details",
            "E/M 99214. Add-on 90836. Psychotherapy time: 10:14 to 10:55, 41 minutes.",
            "codes_only_dictated",
        ),
        (
            "encounter",
            "visit_details",
            "E/M 99214. Add-on 90836. Psychotherapy start time: Not stated.",
            "codes_only_dictated",
        ),
        ("psychotherapy", "psychotherapy_time", "41 minutes.", "codes_only_dictated"),
        (
            "psychotherapy",
            "psychotherapy_time",
            "10:14 to 10:55, 40 minutes.",
            "codes_only_dictated",
        ),
        # Therapy took place, but the section was left empty.
        ("psychotherapy", "issues_addressed", "", "psychotherapy_section"),
        # A risk field paraphrased, judged, or quoting what nobody said.
        ("risk", "self_harm_violence", "Client denies self-harm.", "risk_quoted"),
        # Seen from a real model on this sample: a paraphrase where a quotation belongs.
        (
            "risk",
            "self_harm_violence",
            "The client denied thoughts of hurting themselves or anyone else.",
            "risk_quoted",
        ),
        # Also seen: "anything else?" was asked and answered, but read as never asked.
        ("substance_use", "other_substances", "Not recorded (not asked this visit)", "substances"),
        ("risk", "overall_risk", 'Risk is low ("Denies SI and HI.").', "risk_quoted"),
        ("risk", "suicidal_homicidal_ideation", '"I would never hurt myself."', "risk_quoted"),
        ("risk", "overall_risk", "", "risk_quoted"),
        # The PDMP line says "today", carries no date, the wrong date, or no finding.
        ("plan", "pdmp", "PDMP reviewed today: no early fills, no other prescribers.", "pdmp_line"),
        ("plan", "pdmp", "PDMP reviewed: no early fills, no other prescribers.", "pdmp_line"),
        (
            "plan",
            "pdmp",
            "PDMP reviewed on 2026-03-11: no early fills, no other prescribers.",
            "pdmp_line",
        ),
        ("plan", "pdmp", "PDMP reviewed on March 12, 2026.", "pdmp_line"),
        # The attestation leaves out a location, or loses telehealth.
        (
            "encounter",
            "place_of_service",
            "Telehealth. Client at Client's home in Faketown, AA.",
            "telehealth_attestation",
        ),
        (
            "encounter",
            "place_of_service",
            "Client's home in Faketown, AA; Clinic office at 123 Test St, Faketown, AA.",
            "telehealth_attestation",
        ),
        # An asked substance with no screen, or the baseline lost or rewritten.
        ("substance_use", "cannabis", "Not recorded (not asked this visit)", "substances"),
        ("substance_use", "alcohol", "One to two drinks on weekends.", "substances"),
        ("substance_use", "alcohol", "(asked this visit: no change)", "substances"),
        (
            "substance_use",
            "alcohol",
            "A glass of wine on weekends. (asked this visit: no change)",
            "substances",
        ),
        ("substance_use", "cannabis", "Not recorded (stated this visit: )", "substances"),
        ("substance_use", "cannabis", 'Not recorded (stated this visit: "")', "substances"),
        # A diagnosis or code nobody entered.
        (
            "assessment",
            "diagnoses",
            ["F41.1 Generalized anxiety disorder", "F32.1 Major depressive disorder"],
            "diagnoses_only_stated",
        ),
        (
            "assessment",
            "diagnoses",
            ["F41.1 Generalized anxiety disorder", "Insomnia"],
            "diagnoses_only_stated",
        ),
        ("assessment", "diagnoses", [], "diagnoses_only_stated"),
        ("assessment", "formulation", "Consistent with F41.9.", "diagnoses_only_stated"),
        # A weekday turned into a date.
        ("measures", "measures_reviewed", "GAD-7 on 03/06/2026: 13.", "measures_undated"),
        # A safety plan for a visit with no ideation.
        ("risk", "safety_plan", "Call 988 if thoughts arise.", "safety_plan"),
        # The current list takes today's increase, or drops what the chart has.
        (
            "medications",
            "current_medications",
            ["Adderall XR 20 mg, every morning", "Sertraline 75 mg, every morning"],
            "medications_from_chart",
        ),
        (
            "medications",
            "current_medications",
            ["Adderall XR 20 mg, every morning"],
            "medications_from_chart",
        ),
        (
            "medications",
            "current_medications",
            ["Adderall XR 20mg qAM", "Sertraline 50 mg, every morning"],
            "medications_from_chart",
        ),
        # A history field rewritten from the visit, or left empty when the chart has it.
        (
            "social_history",
            "work_school",
            "Financial analyst; stressed about the quarterly review.",
            "history_from_chart",
        ),
        ("psychiatric_history", "prior_diagnoses", "", "history_from_chart"),
        # Nothing on the chart, but the draft fills it from the visit.
        ("social_history", "relationships", "Supportive partner.", "history_from_chart"),
    ],
)
def test_therapy_draft_failures_are_caught(section: str, key: str, value: Any, check: str) -> None:
    failed = _failed(_with(THERAPY_DRAFT, section, key, value), FOLLOW_UP_WITH_THERAPY)

    assert list(failed) == [check], failed


@pytest.mark.parametrize(
    ("section", "key", "value", "check"),
    [
        # Nothing was dictated: no code, time or minutes may appear.
        ("encounter", "visit_details", "E/M code: 99213.", "codes_only_dictated"),
        ("encounter", "visit_details", "Visit 25 minutes.", "codes_only_dictated"),
        # No therapy: every psychotherapy field stays empty, "Not stated." included.
        ("psychotherapy", "psychotherapy_time", "Not stated.", "psychotherapy_section"),
        ("psychotherapy", "issues_addressed", "Supportive check-in.", "psychotherapy_section"),
        # No level was stated, so none may be written.
        ("risk", "overall_risk", "Low.", "risk_quoted"),
        # No check was dictated, so the PDMP line claims none.
        ("plan", "pdmp", "PDMP reviewed on 2026-03-13: no concerns.", "pdmp_line"),
        # An office visit is not attested as telehealth.
        ("encounter", "place_of_service", "Telehealth visit.", "telehealth_attestation"),
        # Never asked: not a denial.
        (
            "substance_use",
            "tobacco_nicotine",
            'Not recorded (stated this visit: "denies")',
            "substances",
        ),
        ("substance_use", "cannabis", "Not recorded (asked this visit: no change)", "substances"),
        ("substance_use", "cannabis", "", "substances"),
        # Only the diagnosis the clinician named, with no invented code.
        (
            "assessment",
            "diagnoses",
            ["F32.5 Major depressive disorder, in full remission"],
            "diagnoses_only_stated",
        ),
        (
            "assessment",
            "diagnoses",
            ["Depression, in remission", "Generalized anxiety disorder"],
            "diagnoses_only_stated",
        ),
        # No history on the chart: a field reads "Not recorded", not "Not stated.".
        ("trauma_history", "trauma_history", "Not stated.", "history_from_chart"),
        # The chart has bupropion; the list says none, or adds what it does not have.
        ("medications", "current_medications", ["None recorded"], "medications_from_chart"),
        (
            "medications",
            "current_medications",
            ["Bupropion XL 150 mg, every morning", "Sertraline 50 mg"],
            "medications_from_chart",
        ),
    ],
)
def test_medication_only_draft_failures_are_caught(
    section: str, key: str, value: Any, check: str
) -> None:
    failed = _failed(_with(MEDICATION_ONLY_DRAFT, section, key, value), FOLLOW_UP_MEDICATION_ONLY)

    assert list(failed) == [check], failed


@pytest.mark.parametrize(
    "text",
    [
        "reviewed on 2026-03-12",
        "reviewed on 3/12/2026",
        "reviewed on March 12, 2026",
        "on 12 March 2026",
    ],
)
def test_calendar_dates_read_the_forms_a_note_uses(text: str) -> None:
    assert calendar_dates(text) == [date(2026, 3, 12)]


def test_a_weekday_is_not_a_calendar_date() -> None:
    assert calendar_dates("completed on Friday") == []


@pytest.mark.parametrize("case", ALL_CASES, ids=lambda c: c.name)
def test_cases_draft_a_real_template_sample(case: Any) -> None:
    """Each case names a template and sample that exist, with inputs the route accepts."""
    definition = to_definition(practice_key(case.template), 0, case.spec)

    assert validate_note_inputs(definition, case.inputs)
    assert case.transcript.strip()
    fields = {(s.key, f.key) for s in definition.sections for f in s.fields}
    assert {("plan", "pdmp"), ("risk", "overall_risk"), ("encounter", "place_of_service")} <= fields
