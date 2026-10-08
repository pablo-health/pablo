# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Deterministic checks on a visit's chart proposals.

Each check takes the proposals the call returned (after the server-side
checks, so every cited id already exists in the visit) and the case, and
returns the problems it found; none means it passed.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from evals.note_templates.scorers import normalize

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from app.chart_proposals.models import DraftedProposal

    from evals.chart_proposals.cases import ProposalCase


def exactly_the_expected_fields(
    proposals: Sequence[DraftedProposal], case: ProposalCase
) -> list[str]:
    """No proposal is missing, and none fires on a field nothing changed."""
    got = [p.field_key for p in proposals]
    want = {e.field_key for e in case.expected}
    problems = [f"missing a proposal for {key}" for key in sorted(want - set(got))]
    problems += [f"unexpected proposal for {key}" for key in sorted(set(got) - want)]
    problems += [
        f"more than one proposal for {key}" for key in sorted({k for k in got if got.count(k) > 1})
    ]
    return problems


def text_kept_and_changed(proposals: Sequence[DraftedProposal], case: ProposalCase) -> list[str]:
    """Each proposal keeps what the chart said and says what changed: never a removal."""
    problems = []
    for expected in case.expected:
        for proposal in (p for p in proposals if p.field_key == expected.field_key):
            text = normalize(proposal.proposed_text)
            problems += [
                f"{expected.field_key} leaves out {term!r}"
                for term in expected.must_contain
                if normalize(term) not in text
            ]
            if expected.must_contain_any and not any(
                normalize(term) in text for term in expected.must_contain_any
            ):
                problems.append(
                    f"{expected.field_key} says none of {list(expected.must_contain_any)}"
                )
            problems += [
                f"{expected.field_key} says {term!r}"
                for term in expected.must_not_contain
                if normalize(term) in text
            ]
    return problems


def cites_the_lines_that_say_it(
    proposals: Sequence[DraftedProposal], case: ProposalCase
) -> list[str]:
    """Each proposal cites at least one of the lines that state its change."""
    problems = []
    for expected in case.expected:
        for proposal in (p for p in proposals if p.field_key == expected.field_key):
            cited = {e.segment_id for e in proposal.evidence}
            if expected.evidence and not cited & set(expected.evidence):
                problems.append(
                    f"{expected.field_key} cites {sorted(cited)}, none of {list(expected.evidence)}"
                )
    return problems


_GENDERED = re.compile(r"\b(he|she|him|his|her|hers|himself|herself)\b", re.IGNORECASE)


def no_gendered_pronouns(proposals: Sequence[DraftedProposal]) -> list[str]:
    """No proposal calls the client he or she: no case's chart records pronouns, so it
    holds whatever the case."""
    return [
        f"{p.field_key} says {found!r}"
        for p in proposals
        for found in dict.fromkeys(m.group(0) for m in _GENDERED.finditer(p.proposed_text))
    ]


CHECKS: dict[str, Callable[[Sequence[DraftedProposal], ProposalCase], list[str]]] = {
    "exactly_the_expected_fields": exactly_the_expected_fields,
    "text_kept_and_changed": text_kept_and_changed,
    "cites_the_lines_that_say_it": cites_the_lines_that_say_it,
}


def grade(proposals: Sequence[DraftedProposal], case: ProposalCase) -> dict[str, list[str]]:
    found = {name: check(proposals, case) for name, check in CHECKS.items()}
    return {**found, "no_gendered_pronouns": no_gendered_pronouns(proposals)}
