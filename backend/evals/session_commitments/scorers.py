# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Deterministic checks for the session-commitment eval. No model judge.

A case's proposals are matched to its expected commitments, each proposal to at
most one, by what its title says and the lines it cites. Then:

* ``recall``: an expected commitment no proposal matches was missed.
* ``nothing_invented``: a proposal that matches no expected commitment is one
  the clinician never made.
* ``client_work_kept_out``: a proposal naming the client's own work (or another
  person's) has given the clinician a task that is not theirs.
* ``dates_only_when_said``: a matched proposal's due date is the day said, and
  there is none where no single day was said.
* ``no_name_or_pronoun``: a title names the client or calls them he or she; the
  task is already tied to them.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.chart_proposals.models import DraftedProposal

    from evals.session_commitments.cases import CommitmentCase, ExpectedCommitment

_PRONOUNS = re.compile(r"\b(he|she|him|her|his|hers)\b", re.IGNORECASE)


@dataclass
class Graded:
    problems: dict[str, list[str]] = field(default_factory=dict)
    expected: int = 0
    found: int = 0
    proposed: int = 0
    invented: int = 0
    client_work: int = 0
    dated: int = 0
    """Matched proposals graded for their date."""
    dated_right: int = 0

    @property
    def passed(self) -> bool:
        return not any(self.problems.values())


def _matches(proposal: DraftedProposal, expected: ExpectedCommitment) -> bool:
    title = proposal.proposed_text.lower()
    cited = {e.segment_id for e in proposal.evidence}
    return any(word in title for word in expected.any_of) and bool(cited & set(expected.evidence))


def grade(proposals: list[DraftedProposal], case: CommitmentCase) -> Graded:
    graded = Graded(
        problems={
            "recall": [],
            "nothing_invented": [],
            "client_work_kept_out": [],
            "dates_only_when_said": [],
            "no_name_or_pronoun": [],
        },
        expected=len(case.expected),
        proposed=len(proposals),
    )
    unmatched = list(proposals)
    for expected in case.expected:
        hit = next((p for p in unmatched if _matches(p, expected)), None)
        if hit is None:
            graded.problems["recall"].append(f"missed: {expected.name}")
            continue
        unmatched.remove(hit)
        graded.found += 1
        graded.dated += 1
        said = hit.commitment.due_date if hit.commitment is not None else None
        if said == expected.due_date:
            graded.dated_right += 1
        else:
            graded.problems["dates_only_when_said"].append(
                f"{expected.name}: due {said}, said {expected.due_date}"
            )
    for proposal in unmatched:
        graded.invented += 1
        graded.problems["nothing_invented"].append(f"not said: {proposal.proposed_text!r}")
    for proposal in proposals:
        title = proposal.proposed_text.lower()
        work = next((w for w in case.client_work if w in title), None)
        if work is not None:
            graded.client_work += 1
            graded.problems["client_work_kept_out"].append(
                f"{proposal.proposed_text!r} is the client's or another's ({work})"
            )
        if case.client_name.lower() in title or _PRONOUNS.search(proposal.proposed_text):
            graded.problems["no_name_or_pronoun"].append(repr(proposal.proposed_text))
    return graded
