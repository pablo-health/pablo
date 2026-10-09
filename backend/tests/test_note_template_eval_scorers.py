# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""The starting-template eval's checks, run on hand-made drafts without a model."""

from __future__ import annotations

import copy
import re
from datetime import date
from typing import Any

import pytest
from app.chart_history.fields import HISTORY_GROUPS, SUBSTANCE_USE
from app.notes.practice_types import practice_key, to_definition, validate_note_inputs
from evals.note_templates.cases import (
    ALL_CASES,
    EVALUATION_EMPTY_CHART,
    FOLLOW_UP_ALLERGY_DISPUTED,
    FOLLOW_UP_ALLERGY_STATED,
    FOLLOW_UP_EMPTY_CHART,
    FOLLOW_UP_FULL_CHART,
    FOLLOW_UP_INTERLEAVED,
    FOLLOW_UP_MEDICATION_ONLY,
    FOLLOW_UP_RISK_LANGUAGE,
    FOLLOW_UP_STATED_CHANGE,
    FOLLOW_UP_SUPPORTIVE_ONLY,
    FOLLOW_UP_THERAPY_PLAN_NOT_STATED,
    FOLLOW_UP_WITH_THERAPY,
    FOLLOW_UP_WITH_THERAPY_AT_HOME,
)
from evals.note_templates.scorers import (
    allergies_never_dropped,
    calendar_dates,
    chart_fed_fields,
    diagnoses_only_stated,
    grade,
    history_from_chart,
    history_from_visit,
    intake_states_meds,
    medications_from_chart,
    no_attribution_tags,
    numbers,
    risk_as_said,
    safety_plan_only_with_ideation,
    substances,
    suffix_only_where_stated,
    techniques_named,
    telehealth_attestation,
    therapy_grounded,
)

THERAPY_DRAFT: dict[str, dict[str, Any]] = {
    "encounter": {
        "visit_details": (
            "Date of service: 2026-03-12. E/M code: 99214. Psychotherapy add-on code: 90836."
        ),
        "place_of_service": (
            "Visit conducted by synchronous audio and video telehealth on a HIPAA-compliant "
            "platform. The client was in Client\u2019s home in Faketown, AA; the provider "
            "was in Clinic office at 123 Test St, Faketown, AA. The client consented "
            "to receive care by telehealth."
        ),
    },
    "subjective": {
        "chief_complaint": '"The worry has been bad."',
        "anxiety": "Worry worse over the last two weeks, lying in bed going over work.",
        "inattention_hyperactivity": "Focus better on Adderall; finishing reports at work.",
        "insomnia_sleep": "Sleep disrupted by worry, not by the medication.",
        "appetite_eating": "Appetite a little lower at lunch; eats a big dinner.",
        "mania": "Denies decreased need for sleep, racing thoughts or spending.",
        "onset_duration_course": "Worry worse for the last couple of weeks.",
        "functioning": "Getting through reports at work.",
    },
    "substance_use": {
        "alcohol": (
            'One to two drinks on weekends. (stated this visit: "A glass of wine on weekends, '
            'maybe two.")'
        ),
        "tobacco_nicotine": "Not recorded (stated this visit: denied)",
        "cannabis": "Not recorded (stated this visit: denied)",
        "other_substances": "Not recorded (stated this visit: denied)",
        "stimulants": "Not recorded (not asked this visit)",
        "cocaine": "Not recorded (not asked this visit)",
        "opioids": "Not recorded (not asked this visit)",
        "benzodiazepines": "Not recorded (not asked this visit)",
    },
    "risk": {
        "suicidal_homicidal_ideation": (
            "Denies SI and HI. Asked about thoughts of hurting self or others or being better "
            "off dead, the client said: \u201cNo. Nothing like that.\u201d"
        ),
        "self_harm_violence": (
            'Asked about thoughts of hurting self or others, the client said: "No. Nothing like '
            'that."'
        ),
        "risk_protective_factors": "Employed, supportive partner, engaged in treatment.",
        "overall_risk": "Overall acute risk is low.",
        "safety_plan": "",
    },
    "mse": {
        "appearance_behavior": "Well groomed, good eye contact.",
        "orientation": "Alert and oriented x4.",
        "speech": "Normal rate and volume.",
        "mood_affect": "Mood anxious; affect congruent, mildly constricted.",
        "thought_process": "Linear and goal directed.",
        "thought_content": "No hallucinations or delusions. Denies SI and HI.",
        "cognition": "Intact.",
        "insight_judgment": "Good.",
    },
    "measures": {"measures_reviewed": "GAD-7 completed on Friday: 13, up from 8."},
    "medications": {
        "current_medications": [
            "Psychiatric:",
            "Adderall XR 20 mg, every morning",
            "Sertraline 50 mg, every morning",
        ],
        "allergies": "Not recorded",
    },
    "assessment": {
        "diagnoses": [
            "F41.1 Generalized anxiety disorder",
            "F90.0 Attention-deficit hyperactivity disorder, predominantly inattentive type",
        ],
    },
    "plan": {
        "medication_plan": [
            "Continue Adderall XR 20 mg every morning.",
            "Increase sertraline from 50 mg to 75 mg every morning for worsening anxiety.",
        ],
        "pdmp": (
            "State prescription monitoring program (PDMP) reviewed on 2026-03-12: "
            "no early fills, no other prescribers."
        ),
        "education_provided": [
            "Sertraline increase may cause stomach upset or jitteriness the first week.",
            "It can take a few weeks to see the change.",
        ],
    },
    "psychotherapy": {
        "psychotherapy_time": "10:14 to 10:55, 41 minutes.",
        "issues_addressed": "Night-time worry about work performance.",
        "modality_interventions": "CBT: thought record, worry window.",
        "response": "Engaged; belief fell from 90 to 40.",
        "goal_plan": "Worry window daily and a thought record twice this week.",
        "progress": "Used the time blocks four out of five workdays.",
        "therapy_cadence": "This work at each visit.",
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
        "suicidal_homicidal_ideation": (
            "Denies SI and HI. Asked about thoughts of hurting self or others, the client said: "
            '"No."'
        ),
        "self_harm_violence": "Not stated.",
        "risk_protective_factors": "",
        "overall_risk": "Not stated.",
        "safety_plan": "",
    },
    "measures": {"measures_reviewed": ""},
    "medications": {
        "current_medications": ["Bupropion XL 150 mg, every morning"],
        "allergies": "Not recorded",
    },
    "assessment": {"diagnoses": ["Depression, in remission"]},
    "subjective": {
        "depression": "Mood good on bupropion.",
        "insomnia_sleep": "Denies trouble sleeping.",
        "inattention_hyperactivity": "Not discussed.",
        "mania": "Not discussed.",
        "appetite_eating": "Not discussed.",
    },
    "plan": {"pdmp": "", "education_provided": [], "lifestyle_counseling": []},
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
    assert history_from_chart(draft, FOLLOW_UP_WITH_THERAPY) == []
    # The therapy sample states neither, so the marks themselves are flagged.
    assert list(_failed(draft, FOLLOW_UP_WITH_THERAPY)) == ["suffix_only_where_stated"]


def test_an_empty_stated_suffix_is_caught() -> None:
    draft = _with(
        THERAPY_DRAFT,
        "social_history",
        "work_school",
        "Financial analyst, full time, since 2022. (stated this visit: )",
    )
    assert history_from_chart(draft, FOLLOW_UP_WITH_THERAPY)


def test_a_medication_the_client_reports_may_follow_the_charts_list() -> None:
    draft = _with(
        MEDICATION_ONLY_DRAFT,
        "medications",
        "current_medications",
        ["Bupropion XL 150 mg, every morning", '(stated this visit: "melatonin 3 mg")'],
    )
    # The list itself is sound; the mark is wrong only because this visit's
    # client named no other medication.
    assert medications_from_chart(draft, FOLLOW_UP_MEDICATION_ONLY) == []
    assert list(_failed(draft, FOLLOW_UP_MEDICATION_ONLY)) == ["suffix_only_where_stated"]


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
        # A session time nobody dictated, outside the time fields.
        (
            "plan",
            "follow_up",
            "Session started at 10:05; return in four weeks.",
            "codes_only_dictated",
        ),
        ("subjective", "recent_stressors", "Visit from 10:00 to 10:55.", "codes_only_dictated"),
        # A procedure code anywhere in the note.
        ("subjective", "recent_stressors", "Discussed 90837 eligibility.", "codes_only_dictated"),
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
        ("risk", "self_harm_violence", "Client denies self-harm.", "risk_as_said"),
        # Seen from a real model on this sample: a paraphrase where a quotation belongs.
        (
            "risk",
            "self_harm_violence",
            "The client denied thoughts of hurting themselves or anyone else.",
            "risk_as_said",
        ),
        # Also seen: "anything else?" was asked and answered, but read as never asked.
        ("substance_use", "other_substances", "Not recorded (not asked this visit)", "substances"),
        ("risk", "overall_risk", "Moderate.", "risk_as_said"),
        ("risk", "overall_risk", "", "risk_as_said"),
        # A quotation that is not the client's: never said, or the clinician's question.
        (
            "risk",
            "self_harm_violence",
            'The client said: "I would never hurt myself."',
            "risk_as_said",
        ),
        (
            "risk",
            "self_harm_violence",
            'The client denied "thoughts of hurting yourself or anyone else".',
            "risk_as_said",
        ),
        # Seen from a real model on this visit: the safety instruction written as a finding.
        (
            "risk",
            "self_harm_violence",
            'The client said: "No. Nothing like that." Advised to call 988 or 911 if urgent.',
            "risk_as_said",
        ),
        # Also seen: an attribution tag on a chart-fed field's mark, and on the plan.
        (
            "social_history",
            "relationships",
            'Not recorded (stated this visit: "supportive partner" — as noted by clinician '
            "in dictated addendum)",
            "no_attribution_tags",
        ),
        (
            "plan",
            "emergency_instructions",
            "Clinician stated: if mood worsens or thoughts of self-harm appear, call the office, "
            "and if it is urgent, 988 or 911.",
            "no_attribution_tags",
        ),
        # The client's mood quoted where the clinician dictated a finding.
        ("mse", "mood_affect", 'Mood "okay"; affect congruent.', "findings_unquoted"),
        (
            "risk",
            "risk_protective_factors",
            '"Employed, supportive partner, engaged in treatment."',
            "findings_unquoted",
        ),
        # The client's answer quoted twice.
        (
            "risk",
            "suicidal_homicidal_ideation",
            'The client said: "No. Nothing like that." Again: "No. Nothing like that."',
            "findings_unquoted",
        ),
        # Also seen: a grouped denial read as "no change", and a substance never named as asked.
        ("substance_use", "cannabis", "Not recorded (asked this visit: no change)", "substances"),
        (
            "substance_use",
            "tobacco_nicotine",
            "Not recorded (asked this visit: no change)",
            "substances",
        ),
        ("substance_use", "stimulants", "Not recorded (asked this visit: no change)", "substances"),
        (
            "substance_use",
            "other_substances",
            'Not recorded (stated this visit: "No, none of that.")',
            "substances",
        ),
        # The client described their drinking: that is what was said, not "no change".
        (
            "substance_use",
            "alcohol",
            "One to two drinks on weekends. (asked this visit: no change)",
            "substances",
        ),
        # "Located at" a location.
        (
            "encounter",
            "place_of_service",
            "Visit conducted by synchronous audio and video telehealth. The client was located at "
            "Client's home in Faketown, AA; the provider was located at Clinic office at 123 Test "
            "St, Faketown, AA.",
            "telehealth_attestation",
        ),
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
        # The chart's text kept, but marked as changed when nothing was.
        # (Being at home for a telehealth visit says nothing about where the client lives.)
        (
            "social_history",
            "living_situation",
            'Not recorded (stated this visit: "I\'m at home")',
            "suffix_only_where_stated",
        ),
        # The increase left out of the plan.
        (
            "plan",
            "medication_plan",
            ["Continue Adderall XR 20 mg.", "Continue sertraline."],
            "medications_from_chart",
        ),
        # The chart's "Not recorded" dropped.
        ("medications", "allergies", "", "allergies_never_dropped"),
        # The worry the visit was about, left out of its domain.
        ("subjective", "anxiety", "Not discussed.", "hpi_by_domain"),
        ("subjective", "anxiety", "Mood okay.", "hpi_by_domain"),
        # What the clinician explained about the increase, lost.
        ("plan", "education_provided", [], "counseling_only_as_stated"),
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
        ("risk", "overall_risk", "Low.", "risk_as_said"),
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
        # A domain that never came up, written as if it had, or as a denial.
        ("subjective", "mania", "Denies manic symptoms.", "hpi_by_domain"),
        ("subjective", "appetite_eating", "", "hpi_by_domain"),
        # A domain the visit covered, left as not discussed.
        ("subjective", "insomnia_sleep", "Not discussed.", "hpi_by_domain"),
        # Nothing was taught or advised, so nothing is recorded.
        (
            "plan",
            "education_provided",
            ["Reviewed bupropion side effects."],
            "counseling_only_as_stated",
        ),
        (
            "plan",
            "lifestyle_counseling",
            ["Encouraged regular exercise."],
            "counseling_only_as_stated",
        ),
        ("plan", "education_provided", ["None."], "counseling_only_as_stated"),
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


TURN = re.compile(r"^\[\d{2}:\d{2}:\d{2}\] (?:Therapist|Client): \S")


@pytest.mark.parametrize("case", ALL_CASES, ids=lambda c: c.name)
def test_cases_draft_a_real_template(case: Any) -> None:
    """Each case names a template that exists, a visit in its samples' format,
    inputs the route accepts, and only fields the template has."""
    definition = to_definition(practice_key(case.template), 0, case.spec)

    assert validate_note_inputs(definition, case.inputs)
    lines = case.transcript.strip().splitlines()
    assert lines
    assert all(TURN.match(line) for line in lines), case.name
    fields = {(s.key, f.key) for s in definition.sections for f in s.fields}
    assert {("risk", "overall_risk"), ("encounter", "place_of_service")} <= fields
    assert ("medications", "current_medications") in chart_fed_fields(case)
    named = {f"{s}.{k}" for s, k in fields}
    assert set(case.expected.stated_this_visit) <= named
    assert set(case.expected.may_state) <= named
    assert set(case.expected.findings) <= named
    assert set(case.expected.quoted_once) <= named


@pytest.mark.parametrize(
    ("attestation", "passes"),
    [
        (
            "Visit conducted by synchronous audio and video telehealth on a HIPAA-compliant "
            "platform. The client was at home in Michigan; the provider was in Michigan. The "
            "client consented to receive care by telehealth.",
            True,
        ),
        # Seen from a real model: "located at" a state, and the home the client named, lost.
        (
            "Visit conducted by synchronous audio and video telehealth on a HIPAA-compliant "
            "platform. The client was located at Michigan; the provider was located at Michigan.",
            False,
        ),
        ("Telehealth visit. The client was in Michigan; the provider was in Michigan.", False),
    ],
)
def test_the_attestation_says_where_the_client_said_they_were(
    attestation: str, passes: bool
) -> None:
    draft = {"encounter": {"place_of_service": attestation}}

    assert (telehealth_attestation(draft, FOLLOW_UP_WITH_THERAPY_AT_HOME) == []) is passes


@pytest.mark.parametrize("case", ALL_CASES, ids=lambda c: c.name)
def test_every_visit_ends_with_the_clinicians_dictation(case: Any) -> None:
    """The risk, codes and findings each case grades come from the dictated tail."""
    last = case.transcript.strip().splitlines()[-1]

    assert "] Therapist: Note" in last, case.name


def test_the_follow_up_takes_history_from_the_chart_and_the_evaluation_from_the_visit() -> None:
    follow_up = {k for _, k in chart_fed_fields(FOLLOW_UP_FULL_CHART)}
    evaluation = {k for _, k in chart_fed_fields(EVALUATION_EMPTY_CHART)}

    assert {"work_school", "trauma_history", "allergies", "current_medications"} <= follow_up
    assert evaluation == {"allergies", "current_medications"}


# ---------------------------------------------------------------------------
# The chart-fed field rule: the chart's value, then what was stated, marked
# ---------------------------------------------------------------------------


def _chart_history(case: Any, stated: dict[str, str] | None = None) -> dict[str, Any]:
    """Every history field as the case's chart has it, with any stated mark after."""
    recorded = {f.key: f.text for f in case.history}
    draft = _history(recorded)
    for key, mark in (stated or {}).items():
        section = next(g.key for g in HISTORY_GROUPS if key in {f.key for f in g.fields})
        draft[section][key] = f"{draft[section][key]} {mark}"
    return draft


FULL_CHART_DRAFT: dict[str, dict[str, Any]] = {
    **_chart_history(FOLLOW_UP_FULL_CHART),
    "medications": {
        "current_medications": [
            "Psychiatric:",
            "Sertraline 100 mg, every morning",
            "Trazodone 50 mg, at bedtime as needed",
            "Other:",
            "Lisinopril 10 mg, every morning",
        ],
        "allergies": "Sulfa drugs (hives)",
    },
    # The follow-up prints each baseline as recorded, then this visit's screen.
    "substance_use": {
        "alcohol": "Two to three drinks a week, wine with dinner. (asked this visit: no change)",
        "cannabis": "Used in college; none since 2010. (asked this visit: no change)",
        "tobacco_nicotine": "Never. (asked this visit: no change)",
        "stimulants": "Not recorded (not asked this visit)",
        "cocaine": "Not recorded (not asked this visit)",
        "opioids": "Not recorded (not asked this visit)",
        "benzodiazepines": "None. (not asked this visit)",
        "other_substances": "Not recorded (not asked this visit)",
    },
}


def test_a_full_chart_written_as_recorded_passes() -> None:
    for check in (
        history_from_chart,
        medications_from_chart,
        allergies_never_dropped,
        suffix_only_where_stated,
        substances,
    ):
        assert check(FULL_CHART_DRAFT, FOLLOW_UP_FULL_CHART) == [], check.__name__


@pytest.mark.parametrize(
    ("section", "key", "value", "check"),
    [
        # Nothing changed, yet a chart-fed field is marked.
        (
            "social_history",
            "supports",
            'Spouse, two close friends, a sister nearby. (stated this visit: "my husband")',
            "suffix_only_where_stated",
        ),
        (
            "medications",
            "allergies",
            'Sulfa drugs (hives) (stated this visit: "no new allergies")',
            "suffix_only_where_stated",
        ),
        # A history field summarized, or one line of a multi-line field dropped.
        ("family_history", "family_psychiatric", "Father: depression.", "history_from_chart"),
        ("psychiatric_history", "hospitalizations", "Denies.", "history_from_chart"),
        # The recorded allergy dropped.
        ("medications", "allergies", "NKDA", "allergies_never_dropped"),
        # The chart's baseline written as this visit's screen.
        (
            "substance_use",
            "alcohol",
            "Two to three drinks a week, wine with dinner.",
            "substances",
        ),
    ],
)
def test_full_chart_failures_are_caught(section: str, key: str, value: Any, check: str) -> None:
    draft = _with(FULL_CHART_DRAFT, section, key, value)

    assert grade(draft, FOLLOW_UP_FULL_CHART)[check], check


STATED_CHANGE_DRAFT: dict[str, dict[str, Any]] = {
    **_chart_history(
        FOLLOW_UP_STATED_CHANGE,
        {"work_school": '(stated this visit: "I got laid off from the bank three weeks ago")'},
    ),
    "medications": {
        "current_medications": [
            "Escitalopram 10 mg, every morning",
            '"omeprazole, 20 milligrams every morning" (stated this visit)',
        ],
        "allergies": "No known drug allergies (NKDA)",
    },
    "plan": {
        "medication_plan": [
            "Continue escitalopram 10 mg every morning.",
            "Start bupropion XL 150 mg every morning for energy and motivation.",
        ],
    },
}


def test_a_stated_change_kept_after_the_charts_text_passes() -> None:
    for check in (
        history_from_chart,
        medications_from_chart,
        intake_states_meds,
        allergies_never_dropped,
        suffix_only_where_stated,
    ):
        assert check(STATED_CHANGE_DRAFT, FOLLOW_UP_STATED_CHANGE) == [], check.__name__


@pytest.mark.parametrize(
    ("section", "key", "value", "check"),
    [
        # The chart's text replaced by what was said.
        (
            "social_history",
            "work_school",
            "Laid off from the bank three weeks ago.",
            "history_from_chart",
        ),
        # The chart's text kept, and the change not marked at all.
        (
            "social_history",
            "work_school",
            "Bank teller, full time, since 2021.",
            "suffix_only_where_stated",
        ),
        # The change written after the chart's text, but not marked.
        (
            "social_history",
            "work_school",
            "Bank teller, full time, since 2021. Laid off three weeks ago.",
            "history_from_chart",
        ),
        # The reported medication left out, or added without the mark.
        (
            "medications",
            "current_medications",
            ["Escitalopram 10 mg, every morning"],
            "intake_states_meds",
        ),
        (
            "medications",
            "current_medications",
            ["Escitalopram 10 mg, every morning", "Omeprazole 20 mg every morning"],
            "intake_states_meds",
        ),
        # The medication started today put in the current list.
        (
            "medications",
            "current_medications",
            [
                "Escitalopram 10 mg, every morning",
                '"omeprazole, 20 milligrams every morning" (stated this visit)',
                "Bupropion XL 150 mg, every morning (stated this visit)",
            ],
            "medications_from_chart",
        ),
        # ...or missing from the plan.
        (
            "plan",
            "medication_plan",
            ["Continue escitalopram 10 mg."],
            "medications_from_chart",
        ),
    ],
)
def test_stated_change_failures_are_caught(section: str, key: str, value: Any, check: str) -> None:
    draft = _with(STATED_CHANGE_DRAFT, section, key, value)

    assert grade(draft, FOLLOW_UP_STATED_CHANGE)[check], check


@pytest.mark.parametrize(
    ("case", "allergies", "passes"),
    [
        # NKDA on the chart, an allergy stated: both, the statement marked.
        (
            FOLLOW_UP_ALLERGY_STATED,
            'NKDA; "I\'m allergic to amoxicillin" (stated this visit)',
            True,
        ),
        (
            FOLLOW_UP_ALLERGY_STATED,
            'No known drug allergies (NKDA) (stated this visit: "allergic to amoxicillin")',
            True,
        ),
        (FOLLOW_UP_ALLERGY_STATED, "Amoxicillin (rash), reported this visit.", False),
        (FOLLOW_UP_ALLERGY_STATED, "NKDA", False),
        # A recorded allergy disputed: still there, the dispute quoted or not.
        (FOLLOW_UP_ALLERGY_DISPUTED, "Penicillin (hives)", True),
        (
            FOLLOW_UP_ALLERGY_DISPUTED,
            'Penicillin (hives) (stated this visit: "that was actually my brother")',
            True,
        ),
        (FOLLOW_UP_ALLERGY_DISPUTED, "No known drug allergies.", False),
        (FOLLOW_UP_ALLERGY_DISPUTED, "Not recorded", False),
        # Nothing on the chart, a denial stated: "Not recorded", the denial marked.
        (
            EVALUATION_EMPTY_CHART,
            'Not recorded (stated this visit: "No, none that I know of.")',
            True,
        ),
        (
            EVALUATION_EMPTY_CHART,
            "Not recorded (stated this visit: no known drug allergies)",
            True,
        ),
        (EVALUATION_EMPTY_CHART, "No known drug allergies.", False),
        (EVALUATION_EMPTY_CHART, "NKDA", False),
        (EVALUATION_EMPTY_CHART, "Not recorded", False),
    ],
)
def test_allergies_keep_the_chart_and_mark_what_was_said(
    case: Any, allergies: str, passes: bool
) -> None:
    draft = {"medications": {"allergies": allergies}}
    problems = allergies_never_dropped(draft, case) + [
        p for p in suffix_only_where_stated(draft, case) if "allergies" in p
    ]

    assert (problems == []) is passes, problems


EVALUATION_DRAFT: dict[str, dict[str, Any]] = {
    "medications": {
        "current_medications": [
            "None recorded",
            '"Levothyroxine, 75 micrograms every morning" (stated this visit)',
            '"omeprazole, 20 milligrams before breakfast" (stated this visit)',
        ],
        "allergies": 'Not recorded (stated this visit: "No, none that I know of.")',
    },
    "treatment_plan": {
        "plan_items": ["Start escitalopram 5 mg daily for seven days, then 10 mg daily."],
    },
    "assessment": {
        "diagnoses": [
            "F41.0 Panic disorder (stated this visit)",
            "F41.1 Generalized anxiety disorder (stated this visit)",
        ],
    },
    "psychiatric_history": {
        "prior_diagnoses": "Panic disorder, diagnosed by primary care in 2019.",
        "psychotherapy_history": "CBT for about six months in 2020; helped a lot.",
        "medication_trials": ["Sertraline 25 mg: nausea, stopped after two weeks."],
        "hospitalizations": "Denied.",
        "past_self_harm": 'Denied: "No, never."',
        "legal_custody": "Denied.",
    },
    "trauma_history": {"trauma_history": "Car accident at seventeen; avoids highways."},
    "social_history": {
        "living_situation": "Lives with a roommate in an apartment.",
        "relationships": "Single; close with father.",
        "work_school": "Nurse, night shifts at the hospital.",
        "supports": "Father and roommate.",
        "cultural_considerations": "Catholic; church on Sundays matters to the client.",
    },
    "medical_history": {"medical_history": "Hypothyroidism."},
    "family_history": {
        "family_psychiatric": "Aunt: panic attacks.",
        "family_medical": "Father: heart disease.",
    },
}


def test_an_intake_on_an_empty_chart_passes() -> None:
    for check in (
        medications_from_chart,
        intake_states_meds,
        allergies_never_dropped,
        suffix_only_where_stated,
        history_from_chart,
        history_from_visit,
        diagnoses_only_stated,
    ):
        assert check(EVALUATION_DRAFT, EVALUATION_EMPTY_CHART) == [], check.__name__


@pytest.mark.parametrize(
    ("section", "key", "value", "check"),
    [
        # The client listed what they take; the draft says there is nothing.
        ("medications", "current_medications", ["None recorded"], "intake_states_meds"),
        # The client's medications listed, but "None recorded" left out.
        (
            "medications",
            "current_medications",
            [
                '"Levothyroxine 75 mcg" (stated this visit)',
                '"omeprazole 20 mg" (stated this visit)',
            ],
            "medications_from_chart",
        ),
        # Listed as if the chart had them.
        (
            "medications",
            "current_medications",
            ["Levothyroxine 75 mcg every morning", "Omeprazole 20 mg before breakfast"],
            "intake_states_meds",
        ),
        # The escitalopram started today written as current.
        (
            "medications",
            "current_medications",
            [
                "None recorded",
                '"Levothyroxine" (stated this visit)',
                '"omeprazole" (stated this visit)',
                "Escitalopram 5 mg daily",
            ],
            "medications_from_chart",
        ),
        # A history field the visit covered, left as if from an empty chart.
        ("social_history", "work_school", "Not recorded", "history_from_visit"),
        ("trauma_history", "trauma_history", "Denies trauma.", "history_from_visit"),
        # A diagnosis named without the code the clinician gave, or one never named.
        ("assessment", "diagnoses", ["Panic disorder", "F41.1 GAD"], "diagnoses_only_stated"),
        (
            "assessment",
            "diagnoses",
            ["F41.0 Panic disorder", "F41.1 Generalized anxiety disorder", "F90.0 ADHD"],
            "diagnoses_only_stated",
        ),
    ],
)
def test_intake_failures_are_caught(section: str, key: str, value: Any, check: str) -> None:
    draft = _with(EVALUATION_DRAFT, section, key, value)

    assert grade(draft, EVALUATION_EMPTY_CHART)[check], check


EMPTY_CHART_DRAFT: dict[str, dict[str, Any]] = {
    **_history({}),
    "medications": {"current_medications": ["None recorded"], "allergies": "Not recorded"},
    "assessment": {"diagnoses": []},
}


@pytest.mark.parametrize(
    "diagnoses", [[], ["No diagnoses recorded."], ["None recorded"]], ids=["empty", "no", "none"]
)
def test_an_empty_chart_names_no_diagnosis(diagnoses: list[str]) -> None:
    draft = _with(EMPTY_CHART_DRAFT, "assessment", "diagnoses", diagnoses)
    for check in (
        diagnoses_only_stated,
        medications_from_chart,
        allergies_never_dropped,
        history_from_chart,
        suffix_only_where_stated,
    ):
        assert check(draft, FOLLOW_UP_EMPTY_CHART) == [], check.__name__


@pytest.mark.parametrize(
    ("section", "key", "value", "check"),
    [
        ("assessment", "diagnoses", ["Insomnia, improving"], "diagnoses_only_stated"),
        ("assessment", "diagnoses", ["G47.00 Insomnia"], "diagnoses_only_stated"),
        ("medications", "current_medications", [], "medications_from_chart"),
        ("medications", "allergies", "NKDA", "allergies_never_dropped"),
        ("social_history", "living_situation", "Not stated.", "history_from_chart"),
    ],
)
def test_empty_chart_failures_are_caught(section: str, key: str, value: Any, check: str) -> None:
    draft = _with(EMPTY_CHART_DRAFT, section, key, value)

    assert grade(draft, FOLLOW_UP_EMPTY_CHART)[check], check


RISK_DRAFT: dict[str, dict[str, Any]] = {
    "risk": {
        "suicidal_homicidal_ideation": (
            "Asked about thoughts of hurting self or being better off dead, the client said: "
            '"Some nights I think everyone would be better off without me." Passive suicidal '
            "ideation, no intent, no plan. Denies HI."
        ),
        "self_harm_violence": 'The client said: "No, I haven\'t done anything like that."',
        "overall_risk": "Overall acute risk is moderate.",
        "safety_plan": (
            "Warning sign: late-night rumination. Coping: walking the dog, calling their "
            "sister. Crisis contacts: 988 and the office."
        ),
    },
}


def test_the_clients_words_quoted_and_the_clinicians_level_stated_passes() -> None:
    assert risk_as_said(RISK_DRAFT, FOLLOW_UP_RISK_LANGUAGE) == []
    assert no_attribution_tags(RISK_DRAFT, FOLLOW_UP_RISK_LANGUAGE) == []
    assert safety_plan_only_with_ideation(RISK_DRAFT, FOLLOW_UP_RISK_LANGUAGE) == []


@pytest.mark.parametrize(
    ("key", "value", "check"),
    [
        # The clinician's level quoted, judged otherwise, or left out.
        ("overall_risk", '"Overall acute risk is moderate."', risk_as_said),
        ("overall_risk", "Low.", risk_as_said),
        ("overall_risk", "Moderate, possibly high.", risk_as_said),
        ("overall_risk", "Not stated.", risk_as_said),
        # The ideation paraphrased, or a level judged beside it.
        ("suicidal_homicidal_ideation", "Passive SI without plan.", risk_as_said),
        (
            "suicidal_homicidal_ideation",
            '"Some nights I think everyone would be better off without me." Low lethality.',
            risk_as_said,
        ),
        # The clinician's question quoted as if the client had said it.
        (
            "suicidal_homicidal_ideation",
            'The client endorsed "thoughts of hurting yourself or that you\'d be better off dead".',
            risk_as_said,
        ),
        # Seen from a real model: who said what, tagged.
        (
            "suicidal_homicidal_ideation",
            'Clinician asked: "Any thoughts of hurting yourself?" Client responded: "Some nights '
            'I think everyone would be better off without me."',
            no_attribution_tags,
        ),
        ("overall_risk", "Clinician dictated: overall acute risk moderate.", no_attribution_tags),
        # Ideation was reported and a plan described, but none written.
        ("safety_plan", "", safety_plan_only_with_ideation),
    ],
)
def test_risk_failures_are_caught(key: str, value: str, check: Any) -> None:
    draft = _with(RISK_DRAFT, "risk", key, value)

    assert check(draft, FOLLOW_UP_RISK_LANGUAGE)


# ---------------------------------------------------------------------------
# Shapes the model returns
# ---------------------------------------------------------------------------


def test_a_time_the_client_mentions_is_not_a_session_time() -> None:
    """The client said "wears off by nine": the draft may write it as a clock time."""
    draft = _with(
        THERAPY_DRAFT,
        "subjective",
        "side_effects",
        'Reports feeling "a little groggy" after trazodone, which wears off by 9:00 AM.',
    )

    assert _failed(draft, FOLLOW_UP_WITH_THERAPY) == {}


def test_the_time_of_a_practice_between_sessions_is_not_a_session_time() -> None:
    """Seen from a real model: "a worry window at six in the evening" written as a clock time."""
    draft = _with(
        THERAPY_DRAFT,
        "psychotherapy",
        "goal_plan",
        "Between-session practice: a worry window daily at 6:00 PM and a thought record twice.",
    )

    assert _failed(draft, FOLLOW_UP_WITH_THERAPY) == {}
    claimed = _with(THERAPY_DRAFT, "psychotherapy", "goal_plan", "Session ended at 6:00.")
    assert "codes_only_dictated" in _failed(claimed, FOLLOW_UP_WITH_THERAPY)


def test_a_session_time_nobody_dictated_is_caught_in_the_time_fields() -> None:
    for section, key, text in (
        ("encounter", "visit_details", "Date of service: 2026-03-12. 99214, 90836. 10:00."),
        ("psychotherapy", "psychotherapy_time", "10:10 to 10:55, 41 minutes."),
    ):
        failed = _failed(_with(THERAPY_DRAFT, section, key, text), FOLLOW_UP_WITH_THERAPY)
        assert list(failed) == ["codes_only_dictated"], failed


def test_a_chart_line_under_its_heading_on_one_line_is_the_charts_line() -> None:
    draft = {
        "medications": {"current_medications": ["Psychiatric: Lamotrigine 100 mg, twice daily"]}
    }

    assert medications_from_chart(draft, FOLLOW_UP_ALLERGY_STATED) == []
    rewritten = {"medications": {"current_medications": ["Psychiatric: Lamotrigine 100 mg BID"]}}
    assert medications_from_chart(rewritten, FOLLOW_UP_ALLERGY_STATED)


def test_a_diagnosis_returned_as_label_code_and_status_is_read_whole() -> None:
    named = [
        {"label": "Panic disorder", "code": "F41.0", "status": None},
        {"label": "Generalized anxiety disorder", "code": "F41.1", "status": "stated this visit"},
    ]
    draft = _with(EVALUATION_DRAFT, "assessment", "diagnoses", named)
    assert diagnoses_only_stated(draft, EVALUATION_EMPTY_CHART) == []

    invented = [*named, {"label": "Insomnia disorder", "code": None, "status": None}]
    draft = _with(EVALUATION_DRAFT, "assessment", "diagnoses", invented)
    assert diagnoses_only_stated(draft, EVALUATION_EMPTY_CHART)

    none = {"assessment": {"diagnoses": [{"label": "No diagnoses recorded", "code": None}]}}
    assert diagnoses_only_stated(none, FOLLOW_UP_EMPTY_CHART) == []


def test_substances_are_graded_the_way_each_template_writes_them() -> None:
    """The follow-up prints the baseline then the screen; the evaluation, which
    seeds the chart, writes what the client said."""
    answered = {
        "substance_use": {
            "alcohol": "A glass of wine most nights.",
            "cannabis": "Denies.",
            "tobacco_nicotine": "Quit in 2019.",
            "cocaine": "Denies.",
            "opioids": "Denies.",
            "benzodiazepines": "Denies.",
        }
    }
    assert substances(answered, EVALUATION_EMPTY_CHART) == []

    unscreened = {"substance_use": {"alcohol": "Two to three drinks a week, wine with dinner."}}
    assert "substance_use.alcohol: asked, but no screen recorded" in substances(
        unscreened, FOLLOW_UP_FULL_CHART
    )


# ---------------------------------------------------------------------------
# therapy_grounded: the psychotherapy block claims only what the visit shows
# ---------------------------------------------------------------------------

SUPPORTIVE_BLOCK: dict[str, dict[str, Any]] = {
    "psychotherapy": {
        "psychotherapy_time": "3:02 to 3:41.",
        "issues_addressed": "Grief after the death of their mother; nights spent going over "
        "the hospital.",
        "modality_interventions": "Listened; reflected the client's loneliness and the Sunday "
        "calls; two open questions about their mother.",
        "response": 'Tearful, then smiled talking about her laugh: "It felt good to talk '
        'about her today."',
        "goal_plan": "Not stated.",
        "progress": "Not stated.",
        "therapy_cadence": "Some time for this at each visit.",
    }
}

PLAN_NOT_STATED_BLOCK: dict[str, dict[str, Any]] = {
    "psychotherapy": {
        "psychotherapy_time": "18 minutes.",
        "issues_addressed": "Trouble falling asleep; phone in bed; sleeping in on weekends.",
        "modality_interventions": "Psychoeducation on sleep hygiene: wake time sets the body "
        "clock, phone light keeps the brain alert, the bed only for sleep.",
        "response": 'Asked what to do if unable to fall asleep; "Okay. I\'ll try it."',
        "goal_plan": "Get up at the same time every day, weekends too; leave the phone "
        "charging in the kitchen.",
        "progress": "Not stated.",
        "therapy_cadence": "Not stated.",
    }
}


def test_a_grounded_psychotherapy_block_passes() -> None:
    """The three visits written as they happened: CBT named from its steps, a
    listening visit described in plain words, psychoeducation with one thing
    to try and no plan for therapy beyond it."""
    assert therapy_grounded(THERAPY_DRAFT, FOLLOW_UP_WITH_THERAPY) == []
    assert therapy_grounded(SUPPORTIVE_BLOCK, FOLLOW_UP_SUPPORTIVE_ONLY) == []
    assert therapy_grounded(PLAN_NOT_STATED_BLOCK, FOLLOW_UP_THERAPY_PLAN_NOT_STATED) == []


def test_a_visit_with_no_therapy_is_not_graded_here() -> None:
    """An empty block on a medication visit is psychotherapy_section's to judge."""
    assert therapy_grounded(MEDICATION_ONLY_DRAFT, FOLLOW_UP_MEDICATION_ONLY) == []


@pytest.mark.parametrize(
    ("key", "value", "problem"),
    [
        # A technique the visit never shows, named alongside real ones: the
        # false claim an auditor pulls a 90836 note for.
        (
            "modality_interventions",
            "CBT: Socratic questioning, cognitive reframing, behavioral activation.",
            "which the visit does not show",
        ),
        # The time-blocking the client mentioned, renamed as a technique the
        # clinician did not use.
        ("progress", "Behavioral activation: time blocks most days.", "does not show"),
        # Interventions written so vaguely that none of the work is on record.
        ("modality_interventions", "Discussed worry; provided support.", "names none of"),
        # A belief rating nobody gave.
        ("response", "Belief fell from 90 to 30.", "30 is not a number the client gave"),
        # A rating scale the visit never used.
        ("response", "Rated anxiety 7/10 by the end.", "7 is not a number"),
        # The response left without anything the client said or did.
        ("response", "Engaged and receptive.", "carries none of"),
        # A cadence the clinician never set.
        ("therapy_cadence", "Weekly psychotherapy.", "carries none of"),
    ],
)
def test_ungrounded_therapy_claims_are_caught(key: str, value: str, problem: str) -> None:
    draft = _with(THERAPY_DRAFT, "psychotherapy", key, value)
    found = therapy_grounded(draft, FOLLOW_UP_WITH_THERAPY)
    assert any(problem in p for p in found), found


@pytest.mark.parametrize(
    ("key", "value", "problem"),
    [
        # Listening relabeled as a named therapy.
        ("modality_interventions", "Supportive psychotherapy.", "no named technique"),
        # A structured technique invented for a visit that had none.
        ("response", "Responded well to cognitive reframing.", "no named technique"),
        # A mood rating the clinician never asked for.
        ("response", "Mood rated 4/10, tearful.", "4 is not a number"),
        ("response", "Rated sadness at eight.", "8 is not a number"),
        # A goal nobody set.
        ("goal_plan", "Process grief and build supports.", 'should read "Not stated."'),
        # Progress judged on a visit with no goal and no assignment.
        ("progress", "Meaningful progress in processing grief.", 'should read "Not stated."'),
    ],
)
def test_a_listening_visit_is_not_written_up_as_a_technique(
    key: str, value: str, problem: str
) -> None:
    draft = _with(SUPPORTIVE_BLOCK, "psychotherapy", key, value)
    found = therapy_grounded(draft, FOLLOW_UP_SUPPORTIVE_ONLY)
    assert any(problem in p for p in found), found


@pytest.mark.parametrize(
    ("key", "value", "problem"),
    [
        # The next visit's interval passed off as a plan for therapy.
        ("therapy_cadence", "Every six weeks.", 'should read "Not stated."'),
        # A goal nobody set, written as an improvement.
        (
            "goal_plan",
            "Improve sleep onset; same wake time every day.",
            "judges progress the visit does not show",
        ),
        ("goal_plan", "Fall asleep faster: same wake time daily.", "'faster' was not said"),
        # Sleep hygiene relabeled as a protocol the clinician did not deliver.
        ("modality_interventions", "CBT-I with psychoeducation.", "does not show"),
        # Progress judged where nothing moved yet.
        ("progress", "Significant improvement in insight.", "judges progress"),
    ],
)
def test_a_plan_nobody_stated_is_caught(key: str, value: str, problem: str) -> None:
    draft = _with(PLAN_NOT_STATED_BLOCK, "psychotherapy", key, value)
    found = therapy_grounded(draft, FOLLOW_UP_THERAPY_PLAN_NOT_STATED)
    assert any(problem in p for p in found), found


def test_an_outcome_word_is_allowed_inside_a_quotation_of_what_was_said() -> None:
    """The clinician dictated "Depression improving": quoting it is the
    clinician's judgment, not the draft's. The same word unquoted, or in a
    quotation nobody said, is the draft's."""
    quoted = _with(
        PLAN_NOT_STATED_BLOCK, "psychotherapy", "progress", 'Clinician: "Depression improving."'
    )
    assert therapy_grounded(quoted, FOLLOW_UP_THERAPY_PLAN_NOT_STATED) == []

    invented = _with(PLAN_NOT_STATED_BLOCK, "psychotherapy", "progress", '"Sleep has improved."')
    assert therapy_grounded(invented, FOLLOW_UP_THERAPY_PLAN_NOT_STATED)


def test_outcome_words_are_allowed_where_the_visit_shows_progress() -> None:
    """The belief rating fell from 90 to 40 and the time blocks were kept:
    calling that improvement is supported."""
    draft = _with(
        THERAPY_DRAFT, "psychotherapy", "progress", "Improved: belief fell from 90 to 40."
    )
    assert therapy_grounded(draft, FOLLOW_UP_WITH_THERAPY) == []


def test_a_number_said_in_the_visit_passes_where_the_case_lists_none() -> None:
    """Without a case's list, a number in the response must be in the
    transcript: the interleaved visit's eighty and forty are, thirty is not."""
    said = {"psychotherapy": {"response": "Belief fell from 80% to 40%."}}
    assert therapy_grounded(said, FOLLOW_UP_INTERLEAVED) == []

    unsaid = {"psychotherapy": {"response": "Belief fell from 80% to 30%."}}
    assert therapy_grounded(unsaid, FOLLOW_UP_INTERLEAVED) == [
        "psychotherapy.response: 30 is not a number the client gave"
    ]


@pytest.mark.parametrize(
    ("text", "found"),
    [
        ("In the moment, like ninety. Maybe forty.", {90, 40}),
        ("Four out of five workdays", {4, 5}),
        ("90→40 on a 0-100 scale", {90, 40, 0, 100}),
        ("zero to a hundred", {0, 100}),
        ("twenty-five minutes, seventeen days, twenty-one", {25, 17, 21}),
        ("no one; one of them; attention; weighted", set()),
    ],
)
def test_numbers_are_read_in_digits_and_words(text: str, found: set[int]) -> None:
    assert numbers(text) == found


def test_plain_descriptions_name_no_technique() -> None:
    """Listening and reflecting are what the clinician did, not a technique."""
    assert techniques_named("Listened; reflected the client's worry; one open question.") == set()
    assert techniques_named("CBT-informed reframing of the thought") == {
        "CBT",
        "cognitive reframing",
    }
