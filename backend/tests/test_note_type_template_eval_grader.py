# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""The note-type template eval's grading, run without a model."""

from __future__ import annotations

from typing import Any

from app.models import Transcript
from app.notes.client_present import (
    client_present_end,
    segments_from_transcript,
    split_at_boundary,
)
from app.notes.practice_types import PracticeNoteTypeSpec, to_definition
from evals.note_type_templates.cases import (
    FOLLOW_UP_ADDENDUM,
    FOLLOW_UP_REDRAFT,
    FOLLOW_UP_THERAPY_START,
    INTAKE_NEW_CLIENT,
    load_template,
    sample_transcript,
)
from evals.note_type_templates.run import grade, grade_redraft

CASE = INTAKE_NEW_CLIENT

# Therapy labeled from inside the therapy-start case's bounds, and the 52
# minutes the clinician dictated, so only the window is graded.
_ANY_START: dict[str, Any] = {
    "labels": [{"seconds": 725.0, "label": "therapy"}],
    "dictated": {"minutes": 52, "as_dictated": "52 minutes"},
}


def _faithful_draft() -> dict[str, Any]:
    """A draft that meets every expectation of the case."""
    spec = PracticeNoteTypeSpec.model_validate(load_template(CASE.template)["spec"])
    content: dict[str, Any] = {}
    for section in to_definition("custom.x", 0, spec).sections:
        content[section.key] = {}
        for f in section.fields:
            path = f"{section.key}.{f.key}"
            if path in CASE.empty:
                value: Any = ""
            elif path in CASE.not_covered:
                value = "Not asked."
            elif f.kind == "list":
                value = ["Something said."]
            else:
                value = "Something said."
            content[section.key][f.key] = value
    content["assessment"]["diagnoses"] = [
        {"label": "Major depressive disorder", "code": "F32.1", "status": None},
        {"label": "Generalized anxiety disorder", "code": "F41.1", "status": None},
        {"label": "ADHD, inattentive", "code": "F90.0", "status": "Rule-out"},
    ]
    return content


def test_the_case_sample_is_the_templates_own() -> None:
    assert "Billing 90792" in sample_transcript(CASE.template, CASE.sample)


def test_a_faithful_draft_passes() -> None:
    assert grade(CASE, _faithful_draft()) == {"case": CASE.name, "passed": True, "failures": []}


def test_an_uncovered_field_must_say_so() -> None:
    draft = _faithful_draft()
    draft["psychiatric_ros"]["eating"] = "Appetite normal."
    draft["follow_up"]["follow_up"] = ""

    failures = grade(CASE, draft)["failures"]

    assert any("psychiatric_ros.eating was not covered" in f for f in failures)
    assert "follow_up.follow_up is empty" in failures


def test_psychotherapy_on_a_diagnostic_evaluation_fails() -> None:
    draft = _faithful_draft()
    draft["psychotherapy"]["response"] = "Engaged well."

    assert "psychotherapy.response should be empty" in grade(CASE, draft)["failures"]


def test_an_invented_or_missing_code_fails() -> None:
    draft = _faithful_draft()
    draft["assessment"]["diagnoses"] = [
        {"label": "Major depressive disorder", "code": "F32.1", "status": None},
        {"label": "Generalized anxiety disorder", "code": None, "status": None},
        {"label": "ADHD, combined", "code": "F90.2", "status": None},
    ]
    draft["encounter"]["visit_details"] = "Billing 99204."

    failures = grade(CASE, draft)["failures"]

    assert "diagnosis 'Generalized anxiety disorder' has no code" in failures
    assert "diagnosis code 'F90.2' was never stated" in failures
    assert "stated diagnoses missing: ['F41.1', 'F90.0']" in failures
    assert "encounter.visit_details contains '99204', which was never stated" in failures


def test_a_rule_out_must_be_marked() -> None:
    draft = _faithful_draft()
    draft["assessment"]["diagnoses"][2]["status"] = None

    assert "F90.0 is a rule-out but its status is ''" in grade(CASE, draft)["failures"]


def _addendum_draft(ideation: str, self_harm: str) -> dict[str, Any]:
    return {
        "risk": {
            "suicidal_homicidal_ideation": ideation,
            "self_harm_violence": self_harm,
            "overall_risk": 'Clinician stated: "Overall risk is low."',
        },
        "mse": {
            "mood_affect": 'Clinician stated: "Mood better, affect brighter than last visit."',
            "orientation": "Not stated.",
            "cognition": "Not stated.",
            "speech": "",
        },
    }


def test_the_addendum_case_risk_lives_only_in_the_dictated_tail() -> None:
    transcript = FOLLOW_UP_ADDENDUM.transcript or ""
    segments = segments_from_transcript(Transcript(format="txt", content=transcript))
    boundary = client_present_end(segments, client_channel_expected=True)

    assert boundary is not None
    addendum = split_at_boundary(segments, boundary).addendum_lines
    session = split_at_boundary(segments, boundary).session_lines
    assert "denies suicidal ideation" in addendum
    assert "suicid" not in session.lower()
    assert "harm" not in transcript.lower()


def test_a_quoted_addendum_statement_and_a_marked_gap_pass() -> None:
    draft = _addendum_draft(
        'Clinician stated: "Client denies suicidal ideation, intent or plan."', "Not stated."
    )

    assert grade(FOLLOW_UP_ADDENDUM, draft)["failures"] == []


def test_an_unquoted_paraphrase_or_a_filled_gap_fails() -> None:
    draft = _addendum_draft("The client denies suicidal ideation.", "No self-harm.")

    failures = grade(FOLLOW_UP_ADDENDUM, draft)["failures"]

    assert any("does not quote 'denies suicidal ideation'" in f for f in failures)
    assert any("risk.self_harm_violence was not covered" in f for f in failures)


def test_the_confirmed_window_completes_a_time_that_states_only_its_minutes() -> None:
    # The line a draft renders when the clinician dictated only the minutes.
    draft = {"psychotherapy": {"psychotherapy_time": "52 minutes"}}

    assert grade(FOLLOW_UP_THERAPY_START, draft, _ANY_START)["failures"] == []


def test_a_time_the_window_cannot_complete_fails() -> None:
    draft = {"psychotherapy": {"psychotherapy_time": "45 minutes"}}
    proposal = {**_ANY_START, "dictated": {"minutes": 45, "as_dictated": "45 minutes"}}

    failures = grade(FOLLOW_UP_THERAPY_START, draft, proposal)["failures"]

    assert failures == [
        "psychotherapy.psychotherapy_time reads '45 minutes' after confirming "
        "'11:12 AM to 12:04 PM, 52 minutes'"
    ]


def test_a_psychotherapy_time_left_not_stated_elsewhere_fails() -> None:
    draft = {
        "encounter": {
            "visit_details": "E/M code: Not stated.\nPsychotherapy start time: Not stated."
        },
        "psychotherapy": {"psychotherapy_time": "52 minutes"},
    }

    failures = grade(FOLLOW_UP_THERAPY_START, draft, _ANY_START)["failures"]

    assert failures == [
        "encounter.visit_details still says 'Psychotherapy start time: Not stated.' "
        "after the window was confirmed"
    ]


def _plan(labs: str, medications: str) -> dict[str, Any]:
    return {"plan": {"labs": labs, "medications": [medications]}}


def test_a_redraft_that_keeps_every_fact_and_adds_the_dictation_passes() -> None:
    first = _plan("Home blood pressure 124/78.", "30 day supply sent to the usual pharmacy.")
    redrafted = _plan(
        "Home blood pressure 124/78. A home reading in two weeks.",
        "30 day supply sent to the usual pharmacy.",
    )

    assert grade_redraft(FOLLOW_UP_REDRAFT, first, redrafted) == []


def test_a_redraft_that_drops_a_fact_or_the_dictation_fails() -> None:
    first = _plan("Home blood pressure 124 over 78.", "30-day supply to the pharmacy.")
    redrafted = _plan("A home reading later.", "Continue methylphenidate.")

    assert grade_redraft(FOLLOW_UP_REDRAFT, first, redrafted) == [
        "the redraft dropped '124/78'",
        "the redraft dropped '30 day'",
        "the redraft dropped 'pharmacy'",
        "the redraft lacks the dictated 'two weeks'",
    ]


def test_a_fact_the_first_draft_never_had_is_reported() -> None:
    first = _plan("Not stated.", "30 day supply sent to the usual pharmacy.")
    redrafted = _plan("A home reading in two weeks.", "30 day supply sent to the usual pharmacy.")

    assert grade_redraft(FOLLOW_UP_REDRAFT, first, redrafted) == [
        "the first draft lacks '124/78', so keeping it proves nothing"
    ]
