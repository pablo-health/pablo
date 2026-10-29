# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""The session-commitment eval's checks, on hand-made proposals.

A scorer that passes everything reports a perfect score for a call that gives
the clinician the client's homework; each test feeds it one such answer.
"""

from __future__ import annotations

from datetime import date

from app.chart_proposals.families import SESSION_TASK
from app.chart_proposals.models import Commitment, DraftedProposal, Evidence
from evals.session_commitments.cases import CASES_BY_NAME
from evals.session_commitments.scorers import grade

REFERRAL = CASES_BY_NAME["referral-by-friday"]


def _proposal(title: str, line: int, due: date | None = None) -> DraftedProposal:
    return DraftedProposal(
        field_key=SESSION_TASK,
        item_key=f"S{line}",
        proposed_text=title,
        what_changed="",
        evidence=(Evidence(line, REFERRAL.lines[line]),),
        commitment=Commitment(due_date=due, due_said="by Friday" if due else ""),
    )


def test_the_expected_commitment_with_its_day_passes() -> None:
    graded = grade([_proposal("Send a referral to Dr. Okafor", 2, date(2026, 10, 16))], REFERRAL)
    assert graded.passed, graded.problems
    assert (graded.found, graded.dated_right) == (1, 1)


def test_the_clients_homework_given_to_the_clinician_fails() -> None:
    graded = grade(
        [
            _proposal("Send a referral to Dr. Okafor", 2, date(2026, 10, 16)),
            _proposal("Practice the breathing exercise", 3),
        ],
        REFERRAL,
    )
    assert graded.problems["client_work_kept_out"]
    assert graded.problems["nothing_invented"]


def test_a_missed_commitment_fails_recall() -> None:
    graded = grade([], REFERRAL)
    assert graded.problems["recall"] == ["missed: referral"]


def test_a_wrong_or_unsaid_date_fails() -> None:
    wrong = grade([_proposal("Send a referral", 2, date(2026, 10, 17))], REFERRAL)
    undated = grade([_proposal("Send a referral", 2)], REFERRAL)
    assert wrong.problems["dates_only_when_said"]
    assert undated.problems["dates_only_when_said"]


def test_naming_the_client_fails() -> None:
    graded = grade([_proposal("Send Dana's referral", 2, date(2026, 10, 16))], REFERRAL)
    assert graded.problems["no_name_or_pronoun"]
