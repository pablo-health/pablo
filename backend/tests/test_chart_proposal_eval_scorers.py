# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""The chart-proposal eval's checks, on hand-made proposals, passing and failing."""

from __future__ import annotations

from app.chart_proposals.models import DraftedProposal, Evidence
from evals.chart_proposals.cases import (
    CARRIED_BLOCK_IS_STALE,
    DIVORCE_FINALIZED,
    STOPPED_WORKING,
    TRANSFER_NOTE,
    UNCHANGED,
)
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


def test_evidence_from_the_wrong_lines_fails() -> None:
    off = _proposal("legal_custody", "Divorce finalized; shared custody.", 0, 3)
    assert grade([RELATIONSHIPS, off], DIVORCE_FINALIZED)["cites_the_lines_that_say_it"] == [
        "legal_custody cites [0, 3], none of [7, 8]"
    ]


LAID_OFF = "Worked full time as a dental hygienist until August; laid off."


def test_a_proposal_from_a_stale_document_must_cite_the_paragraph_that_disagrees() -> None:
    both = _proposal("work_school", LAID_OFF, 1, 3)
    assert not any(grade([both], CARRIED_BLOCK_IS_STALE).values())
    plan_only = _proposal("work_school", LAID_OFF, 3)
    assert grade([plan_only], CARRIED_BLOCK_IS_STALE)["cites_the_lines_that_say_it"] == [
        "work_school does not cite 1, which disagrees"
    ]


def test_a_medication_in_a_history_field_fails() -> None:
    trials = _proposal("medication_trials", "Buspirone 15 mg twice daily.", 5)
    work = _proposal("work_school", LAID_OFF, 1, 3)
    problems = grade([work, trials], CARRIED_BLOCK_IS_STALE)
    assert problems["says_nothing_it_never_should"] == ["medication_trials says 'buspirone'"]


def test_a_field_either_reading_allows_may_be_proposed_or_not() -> None:
    expected = [
        _proposal("alcohol", "Two glasses of wine per week.", 6),
        _proposal("tobacco_nicotine", "None.", 6),
        _proposal("work_school", "Returned to full-time work.", 2),
        _proposal("allergies", "rash", 8),
    ]
    assert not any(grade(expected, TRANSFER_NOTE).values())
    sister = _proposal("supports", "Close relationship with sister.", 9)
    assert not any(grade([*expected, sister], TRANSFER_NOTE).values())
    trials = _proposal("medication_trials", "Sertraline.", 7)
    assert grade([*expected, trials], TRANSFER_NOTE)["exactly_the_expected_fields"] == [
        "unexpected proposal for medication_trials"
    ]
