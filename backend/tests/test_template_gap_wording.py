# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""The prescriber templates word a visit gap for its field, and code reads every such
wording as a gap.

The templates ask the model to say a field was not covered the way a prescriber
charts that item, and give examples. Each example the instructions offer must
be one :func:`app.notes.visit_gaps.is_visit_gap` recognises, or a draft that
follows the instructions would have its gap read as a finding: proposed to the
chart as the client's history, or kept in front of a risk field's quotation.
"""

from __future__ import annotations

import json
import re
from typing import Any

import pytest
from app.chart_proposals.recorded import recorded_text
from app.notes.spec_templates import TEMPLATES_DIR
from app.notes.visit_gaps import is_visit_gap
from app.services import (
    hpi_section_call,
    note_generation_service,
    psychotherapy_section_call,
    risk_section_call,
)

TEMPLATES = ("psychiatric_follow_up", "psychiatric_evaluation")

_QUOTED = re.compile(r'"([^"]{2,80})"')
_READS_AS_A_GAP = re.compile(r"(?i)^(?:not|none|no)\b|\bnot\b")

NOT_GAPS = frozenset({"None"})
"""Quoted wordings that are findings: "None" is what the data reviewed reads when
nothing was."""


def _strings(node: Any) -> list[str]:
    if isinstance(node, str):
        return [node]
    if isinstance(node, dict):
        return [s for v in node.values() for s in _strings(v)]
    if isinstance(node, list):
        return [s for v in node for s in _strings(v)]
    return []


def _template(name: str) -> dict[str, Any]:
    loaded: dict[str, Any] = json.loads((TEMPLATES_DIR / f"{name}.json").read_text())
    return loaded


def _instructions() -> list[str]:
    texts = [s for name in TEMPLATES for s in _strings(_template(name)["spec"])]
    return [
        *texts,
        risk_section_call._INSTRUCTIONS,
        psychotherapy_section_call._INSTRUCTIONS,
        hpi_section_call._INSTRUCTIONS,
        note_generation_service._ADDENDUM_TAIL,
    ]


def _gap_wordings() -> list[str]:
    """Every quoted wording in the instructions that says something was not done."""
    found = {
        quoted
        for text in _instructions()
        for quoted in _QUOTED.findall(text)
        if _READS_AS_A_GAP.search(quoted) and quoted not in NOT_GAPS
    }
    return sorted(found)


def test_the_instructions_offer_gaps_worded_for_their_field() -> None:
    wordings = set(_gap_wordings())
    assert {
        "Not assessed this visit.",
        "None discussed.",
        "PDMP review not documented this visit.",
        "Safety plan not discussed this visit.",
    } <= wordings


@pytest.mark.parametrize("wording", _gap_wordings())
def test_every_gap_the_instructions_offer_reads_as_a_gap_and_never_reaches_the_chart(
    wording: str,
) -> None:
    assert is_visit_gap(wording)
    assert recorded_text(wording) == ""


@pytest.mark.parametrize("template", TEMPLATES)
def test_a_model_written_field_is_never_told_to_read_the_one_stock_phrase(template: str) -> None:
    """ "Not stated." stays only where code writes or reads it (a billing code, the
    psychotherapy time, the entered place of service) and in the psychotherapy
    block's goal, progress and cadence. Worded for their field there, the
    psychotherapy call drifted out of the note's voice ("The clinician asked")."""
    spec = _template(template)
    assert "Not stated." not in spec["spec"]["system_prompt"]
    code_owned = {"visit_details", "psychotherapy_time", "place_of_service"}
    code_owned |= {"goal_plan", "progress", "therapy_cadence"}
    told = [
        f"{section['key']}.{field['key']}"
        for section in spec["spec"]["sections"]
        for field in section["fields"]
        if '"Not stated."' in (field.get("ai_hint") or "") and field["key"] not in code_owned
    ]
    assert told == []
