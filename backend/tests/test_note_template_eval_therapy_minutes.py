# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""The starting-template eval's therapy-minutes check, run on hand-made proposals."""

from __future__ import annotations

from typing import Any

import pytest
from app.models import Transcript
from app.notes.client_present import segments_from_transcript
from app.notes.visit_times import client_present_turns, labels_to_stored
from evals.note_templates.cases import (
    ALL_CASES,
    FOLLOW_UP_INTERLEAVED,
    FOLLOW_UP_INTERLEAVED_DICTATED,
    FOLLOW_UP_NO_THERAPY_RECORDED,
    FOLLOW_UP_WITH_THERAPY,
    TemplateCase,
)
from evals.note_templates.scorers import (
    recorded_boundary,
    therapy_minutes,
    therapy_minutes_table,
    truth_labels,
)

LABELED = [c for c in ALL_CASES if c.segment_labels is not None]


def _truth(case: TemplateCase) -> dict[float, Any]:
    end = recorded_boundary(case)
    assert end is not None
    segments = segments_from_transcript(Transcript(format="txt", content=case.transcript))
    return dict(truth_labels(case, client_present_turns(segments, end)))


def _proposal(labels: dict[float, Any], minutes: int | None = None) -> dict[str, Any]:
    return {"labels": labels_to_stored(labels), "dictated": {"minutes": minutes}}


@pytest.mark.parametrize("case", LABELED, ids=[c.name for c in LABELED])
def test_every_client_present_turn_has_a_label_and_the_tail_none(case: TemplateCase) -> None:
    end = recorded_boundary(case)
    assert end is not None
    labels = _truth(case)
    segments = segments_from_transcript(Transcript(format="txt", content=case.transcript))
    assert set(labels) == {s.start for s in segments if s.start < end}
    assert any(s.start >= end for s in segments), "the visit has a dictated tail"


def test_the_labeled_sums_are_the_ones_the_visits_state() -> None:
    proposal = _proposal(_truth(FOLLOW_UP_INTERLEAVED))
    assert therapy_minutes_table(FOLLOW_UP_INTERLEAVED, proposal)["labeled"] == 1331
    dictated = _proposal(_truth(FOLLOW_UP_INTERLEAVED_DICTATED), minutes=30)
    assert therapy_minutes_table(FOLLOW_UP_INTERLEAVED_DICTATED, dictated)["labeled"] == 1497


@pytest.mark.parametrize("case", LABELED, ids=[c.name for c in LABELED])
def test_the_true_labels_pass(case: TemplateCase) -> None:
    minutes = int(case.expected.minutes[0]) if case.expected.minutes else None
    assert therapy_minutes(case, _proposal(_truth(case), minutes)) == []


def test_one_turn_off_passes_and_more_fails() -> None:
    labels = _truth(FOLLOW_UP_INTERLEAVED)
    one = {**labels, 25 * 60 + 10.0: "therapy"}  # the PHQ-9 turn read as therapy
    assert therapy_minutes(FOLLOW_UP_INTERLEAVED, _proposal(one)) == []

    # The medication check and the risk screen read as therapy: 275 s more,
    # past the longest turn (20:25 to 24:50, 265 s).
    other = {"medication_management", "screening_risk"}
    misread = {s: "therapy" for s, label in labels.items() if label in other}
    (problem,) = therapy_minutes(FOLLOW_UP_INTERLEAVED, _proposal({**labels, **misread}))
    assert "more than one turn (265s)" in problem


def test_a_visit_with_no_therapy_proposes_none() -> None:
    labels = _truth(FOLLOW_UP_NO_THERAPY_RECORDED)
    assert therapy_minutes(FOLLOW_UP_NO_THERAPY_RECORDED, _proposal(labels)) == []
    some = {**labels, 9.0: "therapy"}
    (problem,) = therapy_minutes(FOLLOW_UP_NO_THERAPY_RECORDED, _proposal(some))
    assert "for a visit with none" in problem


def test_the_tail_is_never_labeled() -> None:
    labels = {**_truth(FOLLOW_UP_INTERLEAVED), 27 * 60 + 30.0: "therapy"}
    problems = therapy_minutes(FOLLOW_UP_INTERLEAVED, _proposal(labels))
    assert any("after the client left" in p for p in problems)


def test_dictated_minutes_are_returned_as_dictated() -> None:
    labels = _truth(FOLLOW_UP_INTERLEAVED_DICTATED)
    assert therapy_minutes(FOLLOW_UP_INTERLEAVED_DICTATED, _proposal(labels, minutes=24)) == [
        "dictated minutes read as 24, not 30"
    ]
    # A sample drafted without a boundary still checks what was dictated.
    assert therapy_minutes(FOLLOW_UP_WITH_THERAPY, {"dictated": {"minutes": 41}}) == []
    assert therapy_minutes(FOLLOW_UP_WITH_THERAPY, None) == [
        "dictated minutes read as None, not 41"
    ]
