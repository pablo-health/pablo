# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""The chart-proposal eval's checks, on hand-made proposals, passing and failing."""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING

from app.chart_proposals.models import DraftedProposal, Evidence, MedicationChange
from evals.chart_proposals.cases import (
    ANOTHER_PRESCRIBER,
    CARRIED_BLOCK_IS_STALE,
    CLIENT_STOPPED,
    DIVORCE_FINALIZED,
    START_AND_STOP,
    STOPPED_WORKING,
    TRANSFER_NOTE,
    UNCHANGED,
)

if TYPE_CHECKING:
    from app.chart_proposals.models import MedicationAction
from evals.chart_proposals.scorers import grade


def _proposal(field_key: str, text: str, *ids: int) -> DraftedProposal:
    return DraftedProposal(
        field_key=field_key,
        proposed_text=text,
        what_changed="Changed",
        evidence=tuple(Evidence(i, "line") for i in ids),
    )


RELATIONSHIPS = _proposal(
    "relationships", "Married; separated June; divorce finalized September 12. Two sons.", 7
)
LEGAL = _proposal("legal_custody", "Divorce finalized September 12; shared custody.", 8)


def test_the_expected_proposals_pass() -> None:
    assert not any(grade([RELATIONSHIPS, LEGAL], DIVORCE_FINALIZED).values())
    assert not any(grade([], UNCHANGED).values())
    stopped = _proposal(
        "work_school", "Worked as a dental hygienist until August; no longer working there.", 1
    )
    assert not any(grade([stopped], STOPPED_WORKING).values())


def test_a_missing_or_extra_proposal_fails() -> None:
    assert grade([RELATIONSHIPS], DIVORCE_FINALIZED)["exactly_the_expected_fields"] == [
        "missing a proposal for legal_custody"
    ]
    assert grade([RELATIONSHIPS], UNCHANGED)["exactly_the_expected_fields"] == [
        "unexpected proposal for relationships"
    ]


def test_a_removal_fails() -> None:
    replaced = _proposal("work_school", "Unemployed; applying to other offices.", 1)
    assert grade([replaced], STOPPED_WORKING)["text_kept_and_changed"] == [
        "work_school leaves out 'dental'",
        "work_school says none of ['no longer', 'stopped', 'until', 'left', 'former', 'let go']",
    ]


def test_a_gendered_pronoun_fails() -> None:
    gendered = _proposal(
        "work_school", "Worked as a dental hygienist until August, when she was let go.", 1
    )
    assert grade([gendered], STOPPED_WORKING)["no_gendered_pronouns"] == ["work_school says 'she'"]
    # A word that only contains one is fine.
    other = _proposal("work_school", "Worked as a dental hygienist there until August.", 1)
    assert grade([other], STOPPED_WORKING)["no_gendered_pronouns"] == []


def test_evidence_from_the_wrong_lines_fails() -> None:
    off = _proposal("legal_custody", "Divorce finalized; shared custody.", 0, 3)
    assert grade([RELATIONSHIPS, off], DIVORCE_FINALIZED)["cites_the_lines_that_say_it"] == [
        "legal_custody cites [0, 3], none of [7, 8]"
    ]


LAID_OFF = "Worked full time as a dental hygienist until August; laid off."


def _plan() -> list[DraftedProposal]:
    """What the stale-block document's plan proposes for the medication list."""
    return [
        _medication("change", "sertraline", "sertraline 100 mg, once daily", 2, 5),
        _medication("start", "buspirone", "buspirone 5 mg, twice daily", 5),
    ]


def test_a_proposal_from_a_stale_document_must_cite_the_paragraph_that_disagrees() -> None:
    both = _proposal("work_school", LAID_OFF, 1, 3)
    assert not any(grade([both, *_plan()], CARRIED_BLOCK_IS_STALE).values())
    plan_only = _proposal("work_school", LAID_OFF, 3)
    assert grade([plan_only, *_plan()], CARRIED_BLOCK_IS_STALE)["cites_the_lines_that_say_it"] == [
        "work_school does not cite 1, which disagrees"
    ]
    sertraline_plan_only = _medication("change", "sertraline", "sertraline 100 mg", 5)
    problems = grade([both, sertraline_plan_only, _plan()[1]], CARRIED_BLOCK_IS_STALE)
    assert problems["cites_the_lines_that_say_it"] == [
        "medications: sertraline does not cite 2, which disagrees"
    ]


def test_a_medication_only_a_stale_block_lists_is_not_proposed() -> None:
    both = _proposal("work_school", LAID_OFF, 1, 3)
    trazodone = _medication("add", "Trazodone", "Trazodone 50 mg, at bedtime", 2)
    assert grade([both, *_plan(), trazodone], CARRIED_BLOCK_IS_STALE)[
        "exactly_the_expected_fields"
    ] == ["unexpected proposal for medications: trazodone"]


def test_a_medication_in_a_history_field_fails() -> None:
    trials = _proposal("medication_trials", "Buspirone 15 mg twice daily.", 5)
    work = _proposal("work_school", LAID_OFF, 1, 3)
    problems = grade([work, trials, *_plan()], CARRIED_BLOCK_IS_STALE)
    assert problems["says_nothing_it_never_should"] == ["medication_trials says 'buspirone'"]


def test_a_field_either_reading_allows_may_be_proposed_or_not() -> None:
    expected = [
        _proposal("alcohol", "Two glasses of wine per week.", 6),
        _proposal("tobacco_nicotine", "None.", 6),
        _proposal("work_school", "Returned to full-time work.", 2),
        replace(_proposal("allergies", "rash", 8), item_key="Penicillin"),
        _medication("add", "Sertraline", "Sertraline 100 mg, daily", 13),
        _medication("add", "Hydroxyzine", "Hydroxyzine 25 mg, at bedtime as needed", 7),
    ]
    assert not any(grade(expected, TRANSFER_NOTE).values())
    sister = _proposal("supports", "Close relationship with sister.", 9)
    assert not any(grade([*expected, sister], TRANSFER_NOTE).values())
    trials = _proposal("medication_trials", "Sertraline.", 7)
    assert grade([*expected, trials], TRANSFER_NOTE)["exactly_the_expected_fields"] == [
        "unexpected proposal for medication_trials"
    ]


def _medication(action: MedicationAction, name: str, text: str, *ids: int) -> DraftedProposal:
    return DraftedProposal(
        field_key="medications",
        item_key=name,
        proposed_text=text,
        what_changed="Changed",
        evidence=tuple(Evidence(i, "line") for i in ids),
        change=MedicationChange(action=action, drug_name=name),
    )


START = _medication("start", "hydroxyzine", "hydroxyzine 25 mg, in the afternoon as needed", 5)
STOP = _medication("stop", "Trazodone", "Stopped: nausea", 4)


def test_medication_proposals_are_named_by_the_medication_and_its_action() -> None:
    assert not any(grade([START, STOP], START_AND_STOP).values())
    assert grade([START], START_AND_STOP)["exactly_the_expected_fields"] == [
        "missing a proposal for medications: trazodone"
    ]
    as_change = _medication("change", "trazodone", "Stopped: nausea", 4)
    assert grade([START, as_change], START_AND_STOP)["the_stated_action"] == [
        "trazodone is a change, not a stop"
    ]
    # A stop the client states is proposed though the clinician decided nothing yet.
    client_stop = _medication("stop", "buspirone", "Stopped: made them dizzy", 1)
    assert not any(grade([client_stop], CLIENT_STOPPED).values())
    assert grade([], CLIENT_STOPPED)["exactly_the_expected_fields"] == [
        "missing a proposal for medications: buspirone"
    ]


def test_an_allowed_field_may_be_proposed_or_not() -> None:
    add = _medication("add", "lisinopril", "lisinopril 10 mg, once a day in the morning", 1)
    history = _proposal("medical_history", "High blood pressure.", 1)

    assert not any(grade([add], ANOTHER_PRESCRIBER).values())
    assert not any(grade([add, history], ANOTHER_PRESCRIBER).values())
    assert grade([add, _proposal("supports", "Brother.", 1)], ANOTHER_PRESCRIBER)[
        "exactly_the_expected_fields"
    ] == ["unexpected proposal for supports"]
