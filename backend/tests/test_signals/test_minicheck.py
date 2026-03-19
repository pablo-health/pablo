# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Tests for the MiniCheck fact-verification escalation signal."""

from __future__ import annotations

import os
from unittest.mock import MagicMock

os.environ["ENVIRONMENT"] = "development"

import pytest
from app.services.signals.minicheck_signal import MiniCheckSignal
from app.services.verification_signals import SignalContext, SignalVerdict


def _make_context() -> SignalContext:
    return SignalContext(claim_key="test.claim")


def _make_signal_with_mock(
    pred_label: int,
    prob: float,
) -> MiniCheckSignal:
    """Create a MiniCheckSignal with a mocked model returning fixed values."""
    signal = MiniCheckSignal()
    mock_model = MagicMock()
    mock_model.score.return_value = ([pred_label], [prob], None, None)
    signal._model = mock_model
    return signal


# ---------------------------------------------------------------------------
# PASS tests
# ---------------------------------------------------------------------------


class TestMiniCheckPASS:
    """Supported claims with high probability -> PASS."""

    def test_high_probability_supported(self) -> None:
        signal = _make_signal_with_mock(pred_label=1, prob=0.90)
        result = signal.check("Work stress", "Work has been stressful", _make_context())
        assert result.verdict == SignalVerdict.PASS
        assert result.signal_name == "minicheck"

    def test_exact_threshold_passes(self) -> None:
        signal = _make_signal_with_mock(pred_label=1, prob=0.75)
        result = signal.check("claim", "segment", _make_context())
        assert result.verdict == SignalVerdict.PASS

    def test_confidence_capped_at_092(self) -> None:
        signal = _make_signal_with_mock(pred_label=1, prob=0.99)
        result = signal.check("claim", "segment", _make_context())
        assert result.confidence == 0.92

    def test_confidence_equals_prob_below_cap(self) -> None:
        signal = _make_signal_with_mock(pred_label=1, prob=0.80)
        result = signal.check("claim", "segment", _make_context())
        assert result.confidence == 0.80

    def test_pass_detail_contains_score(self) -> None:
        signal = _make_signal_with_mock(pred_label=1, prob=0.85)
        result = signal.check("claim", "segment", _make_context())
        assert "0.850" in result.detail
        assert "supported" in result.detail


# ---------------------------------------------------------------------------
# FAIL tests
# ---------------------------------------------------------------------------


class TestMiniCheckFAIL:
    """Unsupported claims with low probability -> FAIL."""

    def test_low_probability_unsupported(self) -> None:
        signal = _make_signal_with_mock(pred_label=0, prob=0.10)
        result = signal.check("claim", "segment", _make_context())
        assert result.verdict == SignalVerdict.FAIL
        assert result.signal_name == "minicheck"

    def test_exact_threshold_fails(self) -> None:
        signal = _make_signal_with_mock(pred_label=0, prob=0.25)
        result = signal.check("claim", "segment", _make_context())
        assert result.verdict == SignalVerdict.FAIL

    def test_zero_probability_fails(self) -> None:
        signal = _make_signal_with_mock(pred_label=0, prob=0.0)
        result = signal.check("claim", "segment", _make_context())
        assert result.verdict == SignalVerdict.FAIL
        assert result.confidence == 0.0

    def test_fail_confidence_is_prob_times_03(self) -> None:
        signal = _make_signal_with_mock(pred_label=0, prob=0.20)
        result = signal.check("claim", "segment", _make_context())
        assert result.confidence == pytest.approx(0.06)

    def test_fail_detail_contains_score(self) -> None:
        signal = _make_signal_with_mock(pred_label=0, prob=0.15)
        result = signal.check("claim", "segment", _make_context())
        assert "unsupported" in result.detail


# ---------------------------------------------------------------------------
# UNCERTAIN tests
# ---------------------------------------------------------------------------


class TestMiniCheckUNCERTAIN:
    """Ambiguous results -> UNCERTAIN."""

    def test_label_1_below_pass_threshold(self) -> None:
        """Supported label but prob below 0.75 -> UNCERTAIN."""
        signal = _make_signal_with_mock(pred_label=1, prob=0.60)
        result = signal.check("claim", "segment", _make_context())
        assert result.verdict == SignalVerdict.UNCERTAIN

    def test_label_0_above_fail_threshold(self) -> None:
        """Unsupported label but prob above 0.25 -> UNCERTAIN."""
        signal = _make_signal_with_mock(pred_label=0, prob=0.40)
        result = signal.check("claim", "segment", _make_context())
        assert result.verdict == SignalVerdict.UNCERTAIN

    def test_uncertain_confidence_is_prob_times_06(self) -> None:
        signal = _make_signal_with_mock(pred_label=1, prob=0.50)
        result = signal.check("claim", "segment", _make_context())
        assert result.confidence == pytest.approx(0.30)

    def test_uncertain_detail_contains_ambiguous(self) -> None:
        signal = _make_signal_with_mock(pred_label=1, prob=0.60)
        result = signal.check("claim", "segment", _make_context())
        assert "ambiguous" in result.detail


# ---------------------------------------------------------------------------
# Boundary tests
# ---------------------------------------------------------------------------


class TestMiniCheckBoundary:
    """Boundary values at exact thresholds."""

    @pytest.mark.parametrize(
        ("label", "prob", "expected_verdict"),
        [
            (1, 0.75, SignalVerdict.PASS),
            (1, 0.74, SignalVerdict.UNCERTAIN),
            (0, 0.25, SignalVerdict.FAIL),
            (0, 0.26, SignalVerdict.UNCERTAIN),
            (1, 0.92, SignalVerdict.PASS),
            (1, 0.99, SignalVerdict.PASS),
            (0, 0.0, SignalVerdict.FAIL),
        ],
    )
    def test_boundary_values(
        self,
        label: int,
        prob: float,
        expected_verdict: SignalVerdict,
    ) -> None:
        signal = _make_signal_with_mock(pred_label=label, prob=prob)
        result = signal.check("claim", "segment", _make_context())
        assert result.verdict == expected_verdict


# ---------------------------------------------------------------------------
# Model interaction tests
# ---------------------------------------------------------------------------


class TestMiniCheckModelInteraction:
    """Verify correct model API usage."""

    def test_model_called_with_correct_format(self) -> None:
        """MiniCheck expects (docs=[segment], claims=[claim])."""
        signal = _make_signal_with_mock(pred_label=1, prob=0.80)
        signal.check("the claim text", "the segment text", _make_context())

        signal._model.score.assert_called_once_with(
            docs=["the segment text"],
            claims=["the claim text"],
        )

    def test_lazy_loading_no_model_at_init(self) -> None:
        signal = MiniCheckSignal()
        assert signal._model is None

    def test_model_reused_across_calls(self) -> None:
        signal = _make_signal_with_mock(pred_label=1, prob=0.80)
        model_ref = signal._model
        signal.check("a", "b", _make_context())
        signal.check("c", "d", _make_context())
        assert signal._model is model_ref

    def test_custom_model_path(self) -> None:
        signal = MiniCheckSignal(model_path="custom/path")
        assert signal._model_path == "custom/path"
