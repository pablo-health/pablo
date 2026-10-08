# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Cases for the note-template eval.

The first two cases draft a starting template's own sample visits, the same
transcripts Settings offers under "Try it", so the eval grades exactly what
a clinician sees there. The rest draft visits written for the eval in the
same format (``visits.py``), each built to put one chart rule under
pressure. Every visit is synthetic: written for the template or the eval,
about no one. The values entered before the visit (locations) and the chart
are invented here, in the same spirit.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date
from functools import cache
from pathlib import Path
from typing import Any

from app.notes.chart_context import (
    ChartContext,
    ChartHistoryField,
    ChartMedication,
    ChartProblem,
)
from app.notes.practice_types import PracticeNoteTypeSpec

from evals.note_templates import visits

TEMPLATES_DIR = (
    Path(__file__).resolve().parents[3]
    / "frontend"
    / "src"
    / "components"
    / "settings"
    / "noteTypes"
    / "templates"
)


@dataclass(frozen=True)
class Diagnosis:
    """A diagnosis the clinician entered or named: its code, if one was given,
    and words any one of which names it."""

    code: str | None
    terms: tuple[str, ...]


@dataclass(frozen=True)
class Expected:
    """What the visit and the entered values support, and nothing more.

    ``codes``, ``times`` and ``minutes`` are what the clinician dictated;
    the draft may carry these and no others. ``pdmp_findings`` is set when
    the clinician said the prescription monitoring program was checked:
    words of the finding the PDMP line must carry. ``telehealth`` holds the
    entered client and provider locations when the visit was by telehealth.
    """

    therapy: bool
    codes: tuple[str, ...] = ()
    times: tuple[str, ...] = ()
    minutes: tuple[str, ...] = ()
    pdmp_findings: tuple[str, ...] | None = None
    telehealth: tuple[str, str] | None = None
    substances_asked: tuple[str, ...] = ()
    substances_not_asked: tuple[str, ...] = ()
    diagnoses: tuple[Diagnosis, ...] = ()
    """Every diagnosis the note may name. A coded one must be named, with its
    code. Empty means none was entered or named, so the note names none."""
    current_medications: tuple[str, ...] | None = None
    """The chart's medication lines as rendered, which the current list must
    reproduce word for word (empty: "None recorded"); ``None`` leaves the
    list ungraded."""
    not_current: tuple[str, ...] = ()
    """Words of a change made in this visit, which belong to the plan and
    must not appear in the current list."""
    in_plan: tuple[str, ...] = ()
    """Medications started or changed in this visit, which the plan must name."""
    stated_medications: tuple[str, ...] = ()
    """Medications the client said they take that the chart does not list:
    each is added to the current list, marked as stated this visit."""
    allergies_stated: tuple[str, ...] = ()
    """What was said about allergies this visit, which the allergies field must
    carry after the chart's value: words, any one of which will do."""
    stated_this_visit: tuple[str, ...] = ()
    """Chart-fed fields (``section.key``) the visit said something new about:
    each must carry the "(stated this visit" mark, and no other may."""
    may_state: tuple[str, ...] = ()
    """Chart-fed fields where the mark is allowed but not required: what was
    said may or may not count as a change (a disputed allergy, say)."""
    history_from_visit: dict[str, tuple[str, ...]] | None = None
    """For a template whose history comes from the visit, not the chart: the
    history fields the visit covered, each with words one of which the field
    must carry. ``None`` when the template's history comes from the chart."""
    ideation: bool = False
    """Suicidal ideation was reported, so the safety plan the clinician
    described must be written."""
    risk_quotes: dict[str, tuple[str, ...]] = field(default_factory=dict)
    """Risk fields whose quotation must carry one of these words."""


@dataclass(frozen=True)
class TemplateCase:
    name: str
    template: str
    """The starting template's file name, without ``.json``."""
    session_date: date
    expected: Expected
    sample: str | None = None
    """The id of one of the template's sample visits, or ``None`` for ``visit``."""
    visit: str | None = None
    """A visit written for the eval, used when there is no ``sample``."""
    inputs: dict[str, str] = field(default_factory=dict)
    problems: tuple[ChartProblem, ...] = ()
    """The chart's problem list the visit is drafted against."""
    allergy_status: str = "not_recorded"
    allergies: tuple[dict[str, str], ...] = ()
    medications: tuple[ChartMedication, ...] = ()
    """The chart's active medications, as the clinician last recorded them."""
    history: tuple[ChartHistoryField, ...] = ()
    """The chart's recorded history fields. Every other history field is "Not recorded"."""

    @property
    def chart(self) -> ChartContext:
        return ChartContext(
            problems=self.problems,
            allergy_status=self.allergy_status,
            allergies=self.allergies,
            medications=self.medications,
            history=self.history,
        )

    @property
    def spec(self) -> PracticeNoteTypeSpec:
        return PracticeNoteTypeSpec.model_validate(_template(self.template)["spec"])

    @property
    def transcript(self) -> str:
        if self.sample is None:
            return self.visit or ""
        samples = _template(self.template)["samples"]
        return str(next(s["transcript"] for s in samples if s["id"] == self.sample))


@cache
def _template(name: str) -> dict[str, Any]:
    data: dict[str, Any] = json.loads((TEMPLATES_DIR / f"{name}.json").read_text())
    return data


TELEHEALTH = {
    "place_of_service": "Telehealth",
    "client_location": "Client's home in Faketown, AA",
    "provider_location": "Clinic office at 123 Test St, Faketown, AA",
}
TELEHEALTH_LOCATIONS = (TELEHEALTH["client_location"], TELEHEALTH["provider_location"])
RECORDED = date(2025, 11, 3)


def _recorded(**texts: str) -> tuple[ChartHistoryField, ...]:
    return tuple(ChartHistoryField(key, text, RECORDED) for key, text in texts.items())


FOLLOW_UP_WITH_THERAPY = TemplateCase(
    name="follow-up-with-therapy",
    template="psychiatric_follow_up",
    sample="with_therapy",
    session_date=date(2026, 3, 12),
    inputs=TELEHEALTH,
    problems=(
        ChartProblem("Generalized anxiety disorder", "F41.1", "active"),
        ChartProblem(
            "Attention-deficit hyperactivity disorder, predominantly inattentive type",
            "F90.0",
            "active",
        ),
    ),
    # The visit raises sertraline from 50 to 75 mg: the current list still
    # reads 50, and the increase is the plan's.
    medications=(
        ChartMedication("Adderall XR", "20 mg", "every morning", "psychiatric"),
        ChartMedication("Sertraline", "50 mg", "every morning", "psychiatric"),
    ),
    # The visit mentions work stress and a partner: the chart's text still stands.
    history=_recorded(
        prior_diagnoses=(
            "ADHD, predominantly inattentive, diagnosed in college; GAD diagnosed 2024."
        ),
        work_school="Financial analyst, full time, since 2022.",
        family_psychiatric="Mother: generalized anxiety.\nFather: none known.",
        alcohol="One to two drinks on weekends.",
    ),
    expected=Expected(
        therapy=True,
        codes=("99214", "90836"),
        times=("10:14", "10:55"),
        minutes=("41",),
        pdmp_findings=("early fill", "other prescriber"),
        telehealth=TELEHEALTH_LOCATIONS,
        substances_asked=("alcohol", "tobacco_nicotine", "cannabis", "other_substances"),
        diagnoses=(
            Diagnosis("F41.1", ("anxiety",)),
            Diagnosis("F90.0", ("attention", "adhd", "hyperactivity")),
        ),
        current_medications=(
            "Adderall XR 20 mg, every morning",
            "Sertraline 50 mg, every morning",
        ),
        not_current=("75",),
        in_plan=("75",),
        # The clinician dictates "supportive partner" as a protective factor;
        # whether that adds to an empty relationships or supports field is a
        # judgment.
        may_state=("social_history.relationships", "social_history.supports"),
    ),
)

FOLLOW_UP_MEDICATION_ONLY = TemplateCase(
    name="follow-up-medication-only",
    template="psychiatric_follow_up",
    sample="medication_only",
    session_date=date(2026, 3, 13),
    inputs={"place_of_service": "In office"},
    medications=(ChartMedication("Bupropion XL", "150 mg", "every morning"),),
    expected=Expected(
        therapy=False,
        # Asked "Any alcohol or anything else?" and answered only about
        # alcohol: tobacco and cannabis never came up. "Anything else" is
        # left ungraded, since it was asked but not answered.
        substances_asked=("alcohol",),
        substances_not_asked=("tobacco_nicotine", "cannabis"),
        diagnoses=(Diagnosis(None, ("depress",)),),
        current_medications=("Bupropion XL 150 mg, every morning",),
    ),
)

FOLLOW_UP_FULL_CHART = TemplateCase(
    name="follow-up-full-chart",
    template="psychiatric_follow_up",
    visit=visits.FULL_CHART,
    session_date=date(2026, 4, 2),
    inputs={"place_of_service": "In office"},
    problems=(
        ChartProblem("Major depressive disorder, recurrent, moderate", "F33.1", "active"),
        ChartProblem("Generalized anxiety disorder", "F41.1", "active"),
    ),
    allergy_status="recorded",
    allergies=({"substance": "Sulfa drugs", "reaction": "hives"},),
    medications=(
        ChartMedication("Sertraline", "100 mg", "every morning", "psychiatric"),
        ChartMedication("Trazodone", "50 mg", "at bedtime as needed", "psychiatric"),
        ChartMedication("Lisinopril", "10 mg", "every morning", "other"),
    ),
    history=_recorded(
        prior_diagnoses="Major depression first diagnosed 2016; generalized anxiety 2018.",
        psychotherapy_history="CBT 2016-2017 with good response; no current therapist.",
        medication_trials="Fluoxetine 20 mg (2016): activation, stopped after a month.",
        hospitalizations="None.",
        past_self_harm="Denies past attempts or self-harm.",
        legal_custody="None.",
        trauma_history="Denies trauma history.",
        living_situation="Lives with spouse and one child in a house.",
        relationships="Married twelve years; supportive spouse.",
        work_school="High school teacher, full time.",
        supports="Spouse, two close friends, a sister nearby.",
        cultural_considerations="Spanish and English at home; prefers English for visits.",
        medical_history="Hypertension, controlled on lisinopril.",
        family_psychiatric="Father: depression.\nMaternal aunt: bipolar disorder.",
        family_medical="Mother: type 2 diabetes.",
        family_treatment_response="Father did well on sertraline.",
        alcohol="Two to three drinks a week, wine with dinner.",
        cannabis="Used in college; none since 2010.",
        tobacco_nicotine="Never.",
        benzodiazepines="None.",
    ),
    expected=Expected(
        therapy=False,
        substances_asked=("alcohol", "cannabis", "tobacco_nicotine"),
        substances_not_asked=(
            "stimulants",
            "cocaine",
            "opioids",
            "benzodiazepines",
            "other_substances",
        ),
        diagnoses=(
            Diagnosis("F33.1", ("depress",)),
            Diagnosis("F41.1", ("anxiety",)),
        ),
        current_medications=(
            "Sertraline 100 mg, every morning",
            "Trazodone 50 mg, at bedtime as needed",
            "Lisinopril 10 mg, every morning",
        ),
    ),
)

FOLLOW_UP_STATED_CHANGE = TemplateCase(
    name="follow-up-stated-change",
    template="psychiatric_follow_up",
    visit=visits.STATED_CHANGE,
    session_date=date(2026, 4, 6),
    inputs=TELEHEALTH,
    problems=(
        ChartProblem("Major depressive disorder, single episode, moderate", "F32.1", "active"),
    ),
    allergy_status="nkda",
    medications=(ChartMedication("Escitalopram", "10 mg", "every morning", "psychiatric"),),
    history=_recorded(
        living_situation="Lives alone in an apartment.",
        relationships="Single; close to two siblings.",
        work_school="Bank teller, full time, since 2021.",
        medical_history="Seasonal allergies.",
        alcohol="One or two beers on weekends.",
    ),
    expected=Expected(
        therapy=False,
        codes=("99214",),
        telehealth=TELEHEALTH_LOCATIONS,
        substances_asked=("alcohol",),
        substances_not_asked=("cannabis", "tobacco_nicotine", "opioids"),
        diagnoses=(Diagnosis("F32.1", ("depress",)),),
        current_medications=("Escitalopram 10 mg, every morning",),
        not_current=("bupropion",),
        in_plan=("bupropion",),
        stated_medications=("omeprazole",),
        stated_this_visit=("social_history.work_school", "medications.current_medications"),
        # The omeprazole is "for heartburn": a condition the medical history
        # does not have, so adding it there is allowed, not required.
        may_state=("medical_history.medical_history",),
    ),
)

FOLLOW_UP_ALLERGY_STATED = TemplateCase(
    name="follow-up-allergy-stated-on-nkda",
    template="psychiatric_follow_up",
    visit=visits.ALLERGY_STATED,
    session_date=date(2026, 4, 8),
    inputs={"place_of_service": "In office"},
    problems=(ChartProblem("Bipolar II disorder", "F31.81", "active"),),
    allergy_status="nkda",
    medications=(ChartMedication("Lamotrigine", "100 mg", "twice daily", "psychiatric"),),
    expected=Expected(
        therapy=False,
        substances_asked=("alcohol", "cannabis"),
        substances_not_asked=("tobacco_nicotine", "opioids", "stimulants"),
        diagnoses=(Diagnosis("F31.81", ("bipolar",)),),
        current_medications=("Lamotrigine 100 mg, twice daily",),
        allergies_stated=("amoxicillin",),
        stated_this_visit=("medications.allergies",),
    ),
)

FOLLOW_UP_ALLERGY_DISPUTED = TemplateCase(
    name="follow-up-allergy-disputed",
    template="psychiatric_follow_up",
    visit=visits.ALLERGY_DISPUTED,
    session_date=date(2026, 4, 9),
    inputs=TELEHEALTH,
    problems=(
        ChartProblem(
            "Attention-deficit hyperactivity disorder, predominantly inattentive type",
            "F90.0",
            "active",
        ),
    ),
    allergy_status="recorded",
    allergies=({"substance": "Penicillin", "reaction": "hives"},),
    medications=(ChartMedication("Atomoxetine", "40 mg", "every morning", "psychiatric"),),
    expected=Expected(
        therapy=False,
        telehealth=TELEHEALTH_LOCATIONS,
        substances_asked=("alcohol",),
        substances_not_asked=("cannabis", "tobacco_nicotine", "stimulants"),
        diagnoses=(Diagnosis("F90.0", ("attention", "adhd", "hyperactivity")),),
        current_medications=("Atomoxetine 40 mg, every morning",),
        # The client says the allergy was never theirs; the chart's entry
        # stays, and the statement may be quoted after it. The same words say
        # a brother has the allergy, and problem sets say the client is in
        # school: both may be added to fields the chart has nothing for. Being
        # at an apartment for the visit says nothing about living situation.
        may_state=(
            "medications.allergies",
            "family_history.family_medical",
            "social_history.work_school",
        ),
    ),
)

FOLLOW_UP_EMPTY_CHART = TemplateCase(
    name="follow-up-empty-chart",
    template="psychiatric_follow_up",
    visit=visits.EMPTY_CHART,
    session_date=date(2026, 4, 10),
    inputs={"place_of_service": "In office"},
    expected=Expected(
        therapy=False,
        substances_asked=("alcohol", "cannabis"),
        substances_not_asked=(
            "stimulants",
            "cocaine",
            "opioids",
            "benzodiazepines",
            "tobacco_nicotine",
            "other_substances",
        ),
        current_medications=(),
    ),
)

EVALUATION_EMPTY_CHART = TemplateCase(
    name="evaluation-empty-chart",
    template="psychiatric_evaluation",
    visit=visits.INTAKE,
    session_date=date(2026, 4, 13),
    inputs={**TELEHEALTH, "visit_code": "Psychiatric diagnostic evaluation (90792)"},
    expected=Expected(
        therapy=False,
        codes=("90792",),
        times=("2:00", "2:55"),
        telehealth=TELEHEALTH_LOCATIONS,
        substances_asked=(
            "alcohol",
            "cannabis",
            "tobacco_nicotine",
            "cocaine",
            "opioids",
            "benzodiazepines",
        ),
        diagnoses=(
            Diagnosis("F41.0", ("panic",)),
            Diagnosis("F41.1", ("generalized anxiety",)),
        ),
        current_medications=(),
        not_current=("escitalopram",),
        in_plan=("escitalopram",),
        stated_medications=("levothyroxine", "omeprazole"),
        # Quoted, or in the chart rule's own words for a stated denial.
        allergies_stated=("none that i know of", "no known drug allergies", "no known allergies"),
        stated_this_visit=("medications.current_medications", "medications.allergies"),
        history_from_visit={
            "prior_diagnoses": ("panic",),
            "psychotherapy_history": ("cbt", "cognitive"),
            "medication_trials": ("sertraline",),
            "hospitalizations": ("no", "never", "deni"),
            "past_self_harm": ("no", "never", "deni"),
            "legal_custody": ("no", "deni"),
            "trauma_history": ("car accident", "motor vehicle"),
            "living_situation": ("roommate",),
            "relationships": ("single", "dad", "father"),
            "work_school": ("nurse",),
            "supports": ("dad", "father", "roommate"),
            "cultural_considerations": ("catholic",),
            "medical_history": ("hypothyroid",),
            "family_psychiatric": ("aunt",),
            "family_medical": ("heart",),
        },
    ),
)

FOLLOW_UP_RISK_LANGUAGE = TemplateCase(
    name="follow-up-risk-language",
    template="psychiatric_follow_up",
    visit=visits.RISK_LANGUAGE,
    session_date=date(2026, 4, 14),
    inputs=TELEHEALTH,
    problems=(ChartProblem("Major depressive disorder, recurrent, moderate", "F33.1", "active"),),
    allergy_status="nkda",
    medications=(ChartMedication("Sertraline", "150 mg", "every morning", "psychiatric"),),
    expected=Expected(
        therapy=False,
        telehealth=TELEHEALTH_LOCATIONS,
        substances_not_asked=("alcohol", "cannabis", "tobacco_nicotine", "opioids"),
        diagnoses=(Diagnosis("F33.1", ("depress",)),),
        current_medications=("Sertraline 150 mg, every morning",),
        not_current=("200",),
        in_plan=("200",),
        ideation=True,
        risk_quotes={
            "suicidal_homicidal_ideation": ("better off without me", "passive suicidal"),
            "overall_risk": ("moderate",),
        },
    ),
)

ALL_CASES: tuple[TemplateCase, ...] = (
    FOLLOW_UP_WITH_THERAPY,
    FOLLOW_UP_MEDICATION_ONLY,
    FOLLOW_UP_FULL_CHART,
    FOLLOW_UP_STATED_CHANGE,
    FOLLOW_UP_ALLERGY_STATED,
    FOLLOW_UP_ALLERGY_DISPUTED,
    FOLLOW_UP_EMPTY_CHART,
    EVALUATION_EMPTY_CHART,
    FOLLOW_UP_RISK_LANGUAGE,
)
