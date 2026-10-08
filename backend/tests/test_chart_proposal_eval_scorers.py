# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""The chart-proposal eval's checks, on hand-made proposals, passing and failing."""

from __future__ import annotations

from app.chart_proposals.models import DraftedProposal, Evidence
from evals.chart_proposals.cases import DIVORCE_FINALIZED, STOPPED_WORKING, UNCHANGED
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
