# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Unit tests for the single metadata-driven diagnostic evaluator (PABLO-6xj.1).

Exercises the ``criteria`` strategy — count thresholds, the cardinal
requirement, gate attestations, and the human-readable unmet reasons — plus
the row->definition loader. No database; the definition is a synthetic,
non-clinical fixture built from a params dict exactly as a stored row would
supply it.
"""

from __future__ import annotations

import pytest
from app.diagnostics.definitions import definition_from_row
from app.diagnostics.evaluator import (
    UnknownEvaluatorTypeError,
    evaluate,
)

from .diagnostics_fixtures import SYNTHETIC_DEFINITION


def _defn():
    return definition_from_row(SYNTHETIC_DEFINITION)


def _all_gates_true(defn) -> dict[str, bool]:
    return {g.key: True for g in defn.gates}


def test_loader_builds_expected_shape():
    defn = _defn()
    assert defn.criterion_keys == {"A1", "A2", "A3", "A4", "B1", "B2"}
    assert defn.gate_keys == {"g1", "g2"}
    assert "T00.1" in defn.icd10_codes


def test_meets_when_thresholds_met_including_cardinal():
    defn = _defn()
    # Group A: A1 (cardinal) + A2 = 2; Group B: B1 = 1; all gates true.
    responses = {"A1": True, "A2": True, "B1": True}
    outcome = evaluate(defn, responses, _all_gates_true(defn))
    assert outcome.meets_criteria is True
    assert outcome.unmet_reasons == ()
    assert outcome.suggested_icd10 == "T00.1"


def test_below_threshold_does_not_meet_and_no_code():
    defn = _defn()
    responses = {"A1": True, "B1": True}  # Group A has only 1 of 2
    outcome = evaluate(defn, responses, _all_gates_true(defn))
    assert outcome.meets_criteria is False
    assert outcome.suggested_icd10 is None
    assert any("at least 2" in r for r in outcome.unmet_reasons)


def test_count_met_without_cardinal_fails_cardinal_rule():
    defn = _defn()
    # Group A reaches its count with two non-cardinal items (A3, A4) — no cardinal.
    responses = {"A3": True, "A4": True, "B1": True}
    outcome = evaluate(defn, responses, _all_gates_true(defn))
    assert outcome.meets_criteria is False
    assert any("core symptom" in r for r in outcome.unmet_reasons)


def test_failing_gate_blocks_determination():
    defn = _defn()
    responses = {"A1": True, "A2": True, "B1": True}
    gates = _all_gates_true(defn)
    gates["g2"] = False
    outcome = evaluate(defn, responses, gates)
    assert outcome.meets_criteria is False
    assert any("Gate two" in r for r in outcome.unmet_reasons)


def test_missing_response_counts_as_not_met():
    defn = _defn()
    # One True plus one explicitly False in Group A -> 1 met, below threshold.
    responses = {"A1": True, "A2": False, "B1": True}
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
