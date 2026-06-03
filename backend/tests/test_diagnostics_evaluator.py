# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Unit tests for the single metadata-driven diagnostic evaluator (PABLO-6xj.1).

Exercises the ``criteria`` strategy — count thresholds, the cardinal
requirement, gate attestations, and the human-readable unmet reasons — plus
the row->definition loader. No database; the definition is built from a params
dict exactly as a stored row would supply it.
"""

from __future__ import annotations

import pytest
from app.diagnostics.definitions import definition_from_row
from app.diagnostics.evaluator import (
    UnknownEvaluatorTypeError,
    evaluate,
)

# An MDD-shaped definition (neutral placeholder labels — content is reviewed
# separately). Group A: 5 of 9, with a required cardinal among A1/A2; five
# gates. This mirrors what a platform ``diagnostic_definitions`` row supplies.
_MDD_PARAMS = {
    "criterion_groups": [
        {
            "key": "A",
            "label": "Core symptoms",
            "min_met": 5,
            "require_cardinal": True,
            "criteria": [
                {"key": "A1", "label": "depressed mood", "cardinal": True},
                {"key": "A2", "label": "loss of interest", "cardinal": True},
                {"key": "A3", "label": "appetite/weight change"},
                {"key": "A4", "label": "sleep disturbance"},
                {"key": "A5", "label": "psychomotor change"},
                {"key": "A6", "label": "fatigue"},
                {"key": "A7", "label": "worthlessness/guilt"},
                {"key": "A8", "label": "reduced concentration"},
                {"key": "A9", "label": "thoughts of death"},
            ],
        }
    ],
    "gates": [
        {"key": "duration", "label": "present at least two weeks"},
        {"key": "impairment", "label": "causes distress or impairment"},
        {"key": "not_substance_medical", "label": "not substance/medical"},
        {"key": "not_psychotic", "label": "not a psychotic disorder"},
        {"key": "no_mania_history", "label": "no manic/hypomanic history"},
    ],
    "icd10_options": [
        {"code": "F32.9", "label": "MDD, single episode, unspecified"},
        {"code": "F33.9", "label": "MDD, recurrent, unspecified"},
    ],
}


def _mdd():
    return definition_from_row(
        {
            "code": "mdd",
            "version": 1,
            "display_name": "Major Depressive Disorder",
            "evaluator_type": "criteria",
            "params": _MDD_PARAMS,
            "suggested_icd10": "F32.9",
        }
    )


def _all_gates_true(defn) -> dict[str, bool]:
    return {g.key: True for g in defn.gates}


def test_loader_builds_expected_shape():
    defn = _mdd()
    assert defn.criterion_keys == {f"A{i}" for i in range(1, 10)}
    assert defn.gate_keys == {
        "duration",
        "impairment",
        "not_substance_medical",
        "not_psychotic",
        "no_mania_history",
    }
    assert "F32.9" in defn.icd10_codes


def test_meets_when_five_including_cardinal_and_all_gates():
    defn = _mdd()
    responses = {"A1": True, "A3": True, "A4": True, "A6": True, "A8": True}
    outcome = evaluate(defn, responses, _all_gates_true(defn))
    assert outcome.meets_criteria is True
    assert outcome.unmet_reasons == ()
    assert outcome.suggested_icd10 == "F32.9"


def test_below_threshold_does_not_meet_and_no_code():
    defn = _mdd()
    responses = {"A1": True, "A3": True, "A4": True}  # only 3
    outcome = evaluate(defn, responses, _all_gates_true(defn))
    assert outcome.meets_criteria is False
    assert outcome.suggested_icd10 is None
    assert any("at least 5" in r for r in outcome.unmet_reasons)


def test_five_without_cardinal_fails_cardinal_rule():
    defn = _mdd()
    # Five non-cardinal symptoms, neither A1 nor A2.
    responses = {"A3": True, "A4": True, "A5": True, "A6": True, "A7": True}
    outcome = evaluate(defn, responses, _all_gates_true(defn))
    assert outcome.meets_criteria is False
    assert any("core symptom" in r for r in outcome.unmet_reasons)


def test_failing_gate_blocks_determination():
    defn = _mdd()
    responses = {"A1": True, "A3": True, "A4": True, "A6": True, "A8": True}
    gates = _all_gates_true(defn)
    gates["impairment"] = False
    outcome = evaluate(defn, responses, gates)
    assert outcome.meets_criteria is False
    assert any("distress or impairment" in r for r in outcome.unmet_reasons)


def test_missing_response_counts_as_not_met():
    defn = _mdd()
    # Four True plus one explicitly False -> 4 met, below threshold.
    responses = {"A1": True, "A3": True, "A4": True, "A6": True, "A8": False}
    outcome = evaluate(defn, responses, _all_gates_true(defn))
    assert outcome.meets_criteria is False


def test_unknown_evaluator_type_raises():
    defn = definition_from_row(
        {
            "code": "x",
            "version": 1,
            "display_name": "X",
            "evaluator_type": "sum_scale",  # not implemented in this engine
            "params": {"criterion_groups": [], "gates": [], "icd10_options": []},
        }
    )
    with pytest.raises(UnknownEvaluatorTypeError):
        evaluate(defn, {}, {})
