# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Unit tests for the outcome measures instrument registry.

Covers scoring, severity band boundary values, validation rejects, and
the is_complete helper.
"""

from __future__ import annotations

import pytest
from app.outcome_measures.instruments import (
    InstrumentValidationError,
    compute_total,
    get_instrument,
    is_complete,
    severity_label,
    validate_item_scores,
)


class TestGetInstrument:
    def test_phq9_registered(self) -> None:
        defn = get_instrument("phq9")
        assert defn is not None
        assert defn.code == "phq9"
        assert defn.item_count == 9
        assert defn.item_max == 3
        assert defn.max_total == 27

    def test_gad7_registered(self) -> None:
        defn = get_instrument("gad7")
        assert defn is not None
        assert defn.code == "gad7"
        assert defn.item_count == 7
        assert defn.max_total == 21

    def test_unknown_returns_none(self) -> None:
        assert get_instrument("bogus") is None


class TestValidateItemScores:
    def test_phq9_all_items_valid(self) -> None:
        defn = get_instrument("phq9")
        assert defn is not None
        scores = {str(i): 1 for i in range(1, 10)}
        validate_item_scores(defn, scores)  # should not raise

    def test_gad7_all_items_valid(self) -> None:
        defn = get_instrument("gad7")
        assert defn is not None
        scores = {str(i): 2 for i in range(1, 8)}
        validate_item_scores(defn, scores)  # should not raise

    def test_unknown_key_raises(self) -> None:
        defn = get_instrument("phq9")
        assert defn is not None
        with pytest.raises(InstrumentValidationError, match="Unknown item key"):
            validate_item_scores(defn, {"0": 1})

    def test_out_of_range_high_raises(self) -> None:
        defn = get_instrument("phq9")
        assert defn is not None
        with pytest.raises(InstrumentValidationError, match="out of range"):
            validate_item_scores(defn, {"1": 4})

    def test_out_of_range_low_raises(self) -> None:
        defn = get_instrument("phq9")
        assert defn is not None
        with pytest.raises(InstrumentValidationError, match="out of range"):
            validate_item_scores(defn, {"1": -1})

    def test_non_integer_value_raises(self) -> None:
        defn = get_instrument("phq9")
        assert defn is not None
        with pytest.raises(InstrumentValidationError, match="must be an integer"):
            validate_item_scores(defn, {"1": "2"})  # type: ignore[arg-type]


class TestComputeTotal:
    def test_phq9_sum(self) -> None:
        defn = get_instrument("phq9")
        assert defn is not None
        scores = {str(i): i % 4 for i in range(1, 10)}  # 1,2,3,0,1,2,3,0,1 = 13
        assert compute_total(defn, scores) == sum(v for v in scores.values())

    def test_all_zeros(self) -> None:
        defn = get_instrument("gad7")
        assert defn is not None
        assert compute_total(defn, {str(i): 0 for i in range(1, 8)}) == 0

    def test_max_score_phq9(self) -> None:
        defn = get_instrument("phq9")
        assert defn is not None
        assert compute_total(defn, {str(i): 3 for i in range(1, 10)}) == 27

    def test_max_score_gad7(self) -> None:
        defn = get_instrument("gad7")
        assert defn is not None
        assert compute_total(defn, {str(i): 3 for i in range(1, 8)}) == 21


class TestSeverityLabelPHQ9:
    """Boundary tests for PHQ-9 severity bands.

    Bands: 0-4 minimal, 5-9 mild, 10-14 moderate, 15-19 moderately severe,
           20-27 severe.
    """

    def test_minimal_low(self) -> None:
        defn = get_instrument("phq9")
        assert defn is not None
        assert severity_label(defn, 0) == "minimal"

    def test_minimal_high(self) -> None:
        defn = get_instrument("phq9")
        assert defn is not None
        assert severity_label(defn, 4) == "minimal"

    def test_mild_low(self) -> None:
        defn = get_instrument("phq9")
        assert defn is not None
        assert severity_label(defn, 5) == "mild"

    def test_mild_high(self) -> None:
        defn = get_instrument("phq9")
        assert defn is not None
        assert severity_label(defn, 9) == "mild"

    def test_moderate_low(self) -> None:
        defn = get_instrument("phq9")
        assert defn is not None
        assert severity_label(defn, 10) == "moderate"

    def test_moderate_high(self) -> None:
        defn = get_instrument("phq9")
        assert defn is not None
        assert severity_label(defn, 14) == "moderate"

    def test_moderately_severe_low(self) -> None:
        defn = get_instrument("phq9")
        assert defn is not None
        assert severity_label(defn, 15) == "moderately severe"

    def test_moderately_severe_high(self) -> None:
        defn = get_instrument("phq9")
        assert defn is not None
        assert severity_label(defn, 19) == "moderately severe"

    def test_severe_low(self) -> None:
        defn = get_instrument("phq9")
        assert defn is not None
        assert severity_label(defn, 20) == "severe"

    def test_severe_high(self) -> None:
        defn = get_instrument("phq9")
        assert defn is not None
        assert severity_label(defn, 27) == "severe"


class TestSeverityLabelGAD7:
    """Boundary tests for GAD-7 severity bands.

    Bands: 0-4 minimal, 5-9 mild, 10-14 moderate, 15-21 severe.
    """

    def test_minimal_low(self) -> None:
        defn = get_instrument("gad7")
        assert defn is not None
        assert severity_label(defn, 0) == "minimal"

    def test_minimal_high(self) -> None:
        defn = get_instrument("gad7")
        assert defn is not None
        assert severity_label(defn, 4) == "minimal"

    def test_mild_low(self) -> None:
        defn = get_instrument("gad7")
        assert defn is not None
        assert severity_label(defn, 5) == "mild"

    def test_mild_high(self) -> None:
        defn = get_instrument("gad7")
        assert defn is not None
        assert severity_label(defn, 9) == "mild"

    def test_moderate_low(self) -> None:
        defn = get_instrument("gad7")
        assert defn is not None
        assert severity_label(defn, 10) == "moderate"

    def test_moderate_high(self) -> None:
        defn = get_instrument("gad7")
        assert defn is not None
        assert severity_label(defn, 14) == "moderate"

    def test_severe_low(self) -> None:
        defn = get_instrument("gad7")
        assert defn is not None
        assert severity_label(defn, 15) == "severe"

    def test_severe_high(self) -> None:
        defn = get_instrument("gad7")
        assert defn is not None
        assert severity_label(defn, 21) == "severe"

    def test_out_of_range_returns_none(self) -> None:
        defn = get_instrument("gad7")
        assert defn is not None
        assert severity_label(defn, 22) is None


class TestIsComplete:
    def test_phq9_complete(self) -> None:
        defn = get_instrument("phq9")
        assert defn is not None
        assert is_complete(defn, {str(i): 1 for i in range(1, 10)}) is True

    def test_phq9_incomplete_missing_items(self) -> None:
        defn = get_instrument("phq9")
        assert defn is not None
        assert is_complete(defn, {str(i): 1 for i in range(1, 8)}) is False

    def test_gad7_complete(self) -> None:
        defn = get_instrument("gad7")
        assert defn is not None
        assert is_complete(defn, {str(i): 0 for i in range(1, 8)}) is True

    def test_empty_scores_incomplete(self) -> None:
        defn = get_instrument("phq9")
        assert defn is not None
        assert is_complete(defn, {}) is False
