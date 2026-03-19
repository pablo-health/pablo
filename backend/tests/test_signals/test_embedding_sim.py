# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Tests for the embedding similarity verification signal."""

import os

os.environ["ENVIRONMENT"] = "development"

import pytest
from app.services.signals.embedding_sim import EmbeddingSimilaritySignal
from app.services.verification_signals import SignalContext, SignalVerdict


def _make_context(embedding_similarity: float) -> SignalContext:
    return SignalContext(
        claim_key="test.claim",
        embedding_similarity=embedding_similarity,
    )


@pytest.fixture
def signal() -> EmbeddingSimilaritySignal:
    return EmbeddingSimilaritySignal()


class TestEmbeddingSimName:
    def test_name(self, signal: EmbeddingSimilaritySignal) -> None:
        assert signal.name == "embedding_sim"


class TestEmbeddingSimPASS:
    """High similarity -> PASS."""

    def test_high_similarity_passes(self, signal: EmbeddingSimilaritySignal) -> None:
        result = signal.check("claim", "segment", _make_context(0.90))
        assert result.verdict == SignalVerdict.PASS
        assert result.signal_name == "embedding_sim"

    def test_exact_threshold_passes(self, signal: EmbeddingSimilaritySignal) -> None:
        result = signal.check("claim", "segment", _make_context(0.85))
        assert result.verdict == SignalVerdict.PASS

    def test_confidence_capped_at_090(self, signal: EmbeddingSimilaritySignal) -> None:
        result = signal.check("claim", "segment", _make_context(0.99))
        assert result.confidence == 0.90

    def test_confidence_equals_sim_below_cap(self, signal: EmbeddingSimilaritySignal) -> None:
        result = signal.check("claim", "segment", _make_context(0.87))
        assert result.confidence == 0.87

    def test_perfect_similarity(self, signal: EmbeddingSimilaritySignal) -> None:
        result = signal.check("claim", "segment", _make_context(1.0))
        assert result.verdict == SignalVerdict.PASS
        assert result.confidence == 0.90


class TestEmbeddingSimFAIL:
    """Low similarity -> FAIL."""

    def test_low_similarity_fails(self, signal: EmbeddingSimilaritySignal) -> None:
        result = signal.check("claim", "segment", _make_context(0.10))
        assert result.verdict == SignalVerdict.FAIL
        assert result.signal_name == "embedding_sim"

    def test_exact_threshold_fails(self, signal: EmbeddingSimilaritySignal) -> None:
        result = signal.check("claim", "segment", _make_context(0.30))
        assert result.verdict == SignalVerdict.FAIL

    def test_zero_similarity_fails(self, signal: EmbeddingSimilaritySignal) -> None:
        result = signal.check("claim", "segment", _make_context(0.0))
        assert result.verdict == SignalVerdict.FAIL
        assert result.confidence == 0.0

    def test_fail_confidence_is_sim_times_03(self, signal: EmbeddingSimilaritySignal) -> None:
        result = signal.check("claim", "segment", _make_context(0.20))
        assert result.confidence == pytest.approx(0.06)


class TestEmbeddingSimUNCERTAIN:
    """Ambiguous range -> UNCERTAIN."""

    def test_mid_range_uncertain(self, signal: EmbeddingSimilaritySignal) -> None:
        result = signal.check("claim", "segment", _make_context(0.60))
        assert result.verdict == SignalVerdict.UNCERTAIN
        assert result.signal_name == "embedding_sim"

    def test_just_above_fail_threshold(self, signal: EmbeddingSimilaritySignal) -> None:
        result = signal.check("claim", "segment", _make_context(0.31))
        assert result.verdict == SignalVerdict.UNCERTAIN

    def test_just_below_pass_threshold(self, signal: EmbeddingSimilaritySignal) -> None:
        result = signal.check("claim", "segment", _make_context(0.84))
        assert result.verdict == SignalVerdict.UNCERTAIN

    def test_uncertain_confidence_is_sim_times_07(
        self,
        signal: EmbeddingSimilaritySignal,
    ) -> None:
        result = signal.check("claim", "segment", _make_context(0.50))
        assert result.confidence == pytest.approx(0.35)


class TestEmbeddingSimBoundary:
    """Boundary values at exact thresholds."""

    @pytest.mark.parametrize(
        ("sim", "expected_verdict"),
        [
            (0.30, SignalVerdict.FAIL),
            (0.31, SignalVerdict.UNCERTAIN),
            (0.84, SignalVerdict.UNCERTAIN),
            (0.85, SignalVerdict.PASS),
        ],
    )
    def test_boundary_values(
        self,
        signal: EmbeddingSimilaritySignal,
        sim: float,
        expected_verdict: SignalVerdict,
    ) -> None:
        result = signal.check("claim", "segment", _make_context(sim))
        assert result.verdict == expected_verdict


class TestEmbeddingSimDetail:
    """Detail strings include similarity value."""

    def test_pass_detail(self, signal: EmbeddingSimilaritySignal) -> None:
        result = signal.check("claim", "segment", _make_context(0.90))
        assert "0.900" in result.detail

    def test_fail_detail(self, signal: EmbeddingSimilaritySignal) -> None:
        result = signal.check("claim", "segment", _make_context(0.10))
        assert "very low" in result.detail

    def test_uncertain_detail(self, signal: EmbeddingSimilaritySignal) -> None:
        result = signal.check("claim", "segment", _make_context(0.50))
        assert "ambiguous" in result.detail
