# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Telling a field the visit did not cover from a finding.

A gap worded for its field ("Not quantified.", "None discussed.") must read as
a gap everywhere a draft is read, and a denial or a result must never read as
one: that would drop a risk finding behind a quotation, empty a psychotherapy
block that was written, or keep a stated history out of the chart.
"""

from __future__ import annotations

import pytest
from app.notes.visit_gaps import is_visit_gap


@pytest.mark.parametrize(
    "text",
    [
        "Not stated.",
        "Not discussed.",
        "Not asked.",
        "Not reported.",
        "Not quantified.",
        "Not answered",
        "Not assessed this visit.",
        "Not explicitly stated as difficult.",
        "Sleep onset not explicitly stated as difficult.",
        "Severity not quantified.",
        "PDMP review not documented this visit.",
        "(dose not specified)",
        "None discussed.",
        "None named.",
        "No collateral information obtained during this visit.",
        "No goal or assignment discussed.",
        "No therapy frequency discussed this visit.",
        "  not STATED.  ",
    ],
)
def test_a_gap_worded_for_its_field_reads_as_a_gap(text: str) -> None:
    assert is_visit_gap(text)


@pytest.mark.parametrize(
    "text",
    [
        "",
        "None.",
        "Denies.",
        "Denies SI and HI.",
        "No SI reported.",
        "No suicidal ideation, intent or plan.",
        "Denies SI and HI not assessed.",
        "Not assessed this visit, but endorsed passive thoughts of death.",
        "Not assessed this visit. Endorsed passive thoughts of death.",
        "Overall acute risk is low.",
        "Client is not suicidal.",
        "Hallucinations not observed.",
        "Married 19 years.",
        "Escitalopram 10 mg daily.",
        "Not recorded (stated this visit: lives with a partner)",
    ],
)
def test_a_finding_never_reads_as_a_gap(text: str) -> None:
    assert not is_visit_gap(text)
