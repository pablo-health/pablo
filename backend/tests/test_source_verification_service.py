# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Tests for the hybrid multi-signal source verification pipeline."""

import os

os.environ["ENVIRONMENT"] = "development"

from app.services.embedding_service import MockEmbeddingService
from app.services.nli_service import MockNLIService, NLIResult
from app.services.source_verification_service import (
    CandidateResult,
    SourceVerificationService,
    VerificationResult,
    _interleave,
    _score_to_level,
)
from app.services.verification_signals import (
    SignalContext,
    SignalResult,
    SignalVerdict,
    VerificationSignal,
)

SEGMENTS = [
    "How have you been feeling?",
    "Work has been really stressful.",
    "I feel anxious most days.",
    "That sounds difficult.",
    "I have trouble sleeping.",
]


# ---------------------------------------------------------------------------
# Mock signals for testing
# ---------------------------------------------------------------------------


class _PassSignal(VerificationSignal):
    """Always returns PASS with a configurable confidence."""

    def __init__(self, confidence: float = 0.85) -> None:
        self._confidence = confidence

    @property
    def name(self) -> str:
        return "mock_pass"

    def check(
        self,
        _claim_text: str,
        _segment_text: str,
        _context: SignalContext,
    ) -> SignalResult:
        return SignalResult(
            verdict=SignalVerdict.PASS,
            confidence=self._confidence,
            signal_name=self.name,
            detail="Mock pass",
        )


class _FailSignal(VerificationSignal):
    """Always returns FAIL with a configurable confidence."""

    def __init__(self, confidence: float = 0.05) -> None:
        self._confidence = confidence

    @property
    def name(self) -> str:
        return "mock_fail"

    def check(
        self,
        _claim_text: str,
        _segment_text: str,
        _context: SignalContext,
    ) -> SignalResult:
        return SignalResult(
            verdict=SignalVerdict.FAIL,
            confidence=self._confidence,
            signal_name=self.name,
            detail="Mock fail",
        )


class _UncertainSignal(VerificationSignal):
    """Always returns UNCERTAIN."""

    @property
    def name(self) -> str:
        return "mock_uncertain"

    def check(
        self,
        _claim_text: str,
        _segment_text: str,
        _context: SignalContext,
    ) -> SignalResult:
        return SignalResult(
            verdict=SignalVerdict.UNCERTAIN,
            confidence=0.0,
            signal_name=self.name,
            detail="Mock uncertain",
        )


class _TrackingSignal(VerificationSignal):
    """Records calls for verification, always returns UNCERTAIN."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    @property
    def name(self) -> str:
        return "tracking"

    def check(
        self,
        claim_text: str,
        segment_text: str,
        _context: SignalContext,
    ) -> SignalResult:
        self.calls.append((claim_text, segment_text))
        return SignalResult(
            verdict=SignalVerdict.UNCERTAIN,
            confidence=0.0,
            signal_name=self.name,
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_service(
    default_label: str = "entailment",
    default_score: float = 0.9,
    primary_signals: list[VerificationSignal] | None = None,
    safety_signals: list[VerificationSignal] | None = None,
) -> tuple[SourceVerificationService, MockNLIService]:
    """Create a verification service with mock dependencies."""
    embedding = MockEmbeddingService()
    nli = MockNLIService(default_label=default_label, default_score=default_score)
    return SourceVerificationService(
        embedding,
        nli,
        primary_signals=primary_signals,
        safety_signals=safety_signals,
    ), nli


# ---------------------------------------------------------------------------
# NLI fallback tests (backward compatibility)
# ---------------------------------------------------------------------------


class TestNLIFallbackHighConfidence:
    """NLI fallback: correct attributions get high confidence."""

    def test_correct_attribution_high_confidence(self) -> None:
        service, _nli = _make_service(default_label="entailment", default_score=0.92)
        claims = {"subjective.chief_complaint": "Work has been stressful."}
        attributions = {"subjective.chief_complaint": [1]}

        results = service.verify_attributions(claims, SEGMENTS, attributions)

        assert len(results) == 1
        r = results[0]
        assert r.claim_key == "subjective.chief_complaint"
        assert r.confidence_score == 0.92
        assert r.confidence_level == "high"
        assert r.original_segment_ids == [1]

    def test_multiple_claims_verified(self) -> None:
        service, _nli = _make_service(default_label="entailment", default_score=0.92)
        claims = {
            "subjective.chief_complaint": "Work stress.",
            "subjective.mood_affect": "Anxious.",
        }
        attributions = {
            "subjective.chief_complaint": [1],
            "subjective.mood_affect": [2],
        }

        results = service.verify_attributions(claims, SEGMENTS, attributions)
        assert len(results) == 2
        assert all(r.confidence_level == "high" for r in results)


class TestNLIFallbackLowConfidence:
    """NLI fallback: wrong attributions get low confidence."""

    def test_wrong_attribution_low_confidence(self) -> None:
        service, nli = _make_service(default_label="contradiction", default_score=0.1)

        nli.set_response(
            "That sounds difficult.",
            "Patient has insomnia.",
            NLIResult(
                label="contradiction",
                entailment_score=0.05,
                contradiction_score=0.85,
                neutral_score=0.10,
            ),
        )

        claims = {"objective.behavior": "Patient has insomnia."}
        attributions = {"objective.behavior": [3]}

        results = service.verify_attributions(claims, SEGMENTS, attributions)
        r = results[0]
        assert r.confidence_score < 0.2
        assert r.confidence_level == "unverified"


class TestNLIFallbackNoAttribution:
    """NLI fallback: no attributions -> unverified."""

    def test_no_attribution_unverified(self) -> None:
        service, _nli = _make_service(default_label="entailment", default_score=0.9)
        claims = {"plan.next_session": "Follow up in one week."}
        attributions: dict[str, list[int]] = {"plan.next_session": []}

        results = service.verify_attributions(claims, SEGMENTS, attributions)
        r = results[0]
        assert r.confidence_level == "unverified"
        assert r.original_segment_ids == []


class TestPossibleMatches:
    """Stage 1 candidates that score well but weren't attributed."""

    def test_possible_matches_populated(self) -> None:
        service, nli = _make_service(default_label="entailment", default_score=0.7)

        nli.set_response(
            "How have you been feeling?",
            "Patient reports anxiety about work.",
            NLIResult(
                label="neutral",
                entailment_score=0.3,
                contradiction_score=0.1,
                neutral_score=0.6,
            ),
        )

        claims = {"subjective.chief_complaint": "Patient reports anxiety about work."}
        attributions = {"subjective.chief_complaint": [0]}

        results = service.verify_attributions(claims, SEGMENTS, attributions)
        r = results[0]
        assert r.confidence_score == 0.3
        assert r.confidence_level == "low"
        assert len(r.possible_match_segment_ids) > 0
        assert 0 not in r.possible_match_segment_ids


class TestEdgeCases:
    """Edge cases and empty inputs."""

    def test_empty_claims(self) -> None:
        service, _nli = _make_service()
        results = service.verify_attributions({}, SEGMENTS, {})
        assert results == []

    def test_empty_segments(self) -> None:
        service, _nli = _make_service()
        claims = {"subjective.chief_complaint": "Anxiety."}
        results = service.verify_attributions(claims, [], {"subjective.chief_complaint": [0]})
        assert len(results) == 1
        assert results[0].confidence_level == "unverified"

    def test_out_of_bounds_attribution_skipped(self) -> None:
        service, _nli = _make_service(default_label="entailment", default_score=0.92)
        claims = {"subjective.chief_complaint": "Anxiety."}
        attributions = {"subjective.chief_complaint": [99]}

        results = service.verify_attributions(claims, SEGMENTS, attributions)
        r = results[0]
        assert r.original_segment_ids == [99]


class TestThresholds:
    """Verify confidence level thresholds are applied correctly."""

    def test_verified_threshold(self) -> None:
        assert _score_to_level(0.97) == "verified"
        assert _score_to_level(0.99) == "verified"
        assert _score_to_level(1.0) == "verified"

    def test_high_threshold(self) -> None:
        assert _score_to_level(0.90) == "high"
        assert _score_to_level(0.96) == "high"

    def test_medium_threshold(self) -> None:
        assert _score_to_level(0.60) == "medium"
        assert _score_to_level(0.89) == "medium"

    def test_low_threshold(self) -> None:
        assert _score_to_level(0.30) == "low"
        assert _score_to_level(0.59) == "low"

    def test_unverified_threshold(self) -> None:
        assert _score_to_level(0.0) == "unverified"
        assert _score_to_level(0.29) == "unverified"


class TestInterleave:
    """Test the interleave helper."""

    def test_equal_length(self) -> None:
        assert _interleave([1, 3], [2, 4]) == [1, 2, 3, 4]

    def test_first_longer(self) -> None:
        assert _interleave([1, 3, 5], [2]) == [1, 2, 3, 5]

    def test_second_longer(self) -> None:
        assert _interleave([1], [2, 4, 6]) == [1, 2, 4, 6]

    def test_both_empty(self) -> None:
        assert _interleave([], []) == []


class TestVerificationResultDefaults:
    """Verify VerificationResult default values."""

    def test_defaults(self) -> None:
        r = VerificationResult(claim_key="test", original_segment_ids=[])
        assert r.confidence_score == 0.0
        assert r.confidence_level == "unverified"
        assert r.possible_match_segment_ids == []
        assert r.signal_used == ""


# ---------------------------------------------------------------------------
# CandidateResult tests
# ---------------------------------------------------------------------------


class TestCandidateResult:
    """Test the CandidateResult dataclass."""

    def test_candidate_result_structure(self) -> None:
        cr = CandidateResult(
            candidate_ids={"claim_a": [0, 1, 2]},
            embedding_scores={"claim_a": {0: 0.9, 1: 0.7, 2: 0.3}},
            claim_embeddings=[[0.1, 0.2]],
            segment_embeddings=[[0.3, 0.4], [0.5, 0.6], [0.7, 0.8]],
        )
        assert cr.candidate_ids["claim_a"] == [0, 1, 2]
        assert cr.embedding_scores["claim_a"][0] == 0.9
        assert len(cr.claim_embeddings) == 1
        assert len(cr.segment_embeddings) == 3

    def test_retrieve_candidates_returns_candidate_result(self) -> None:
        """_retrieve_candidates_with_scores returns CandidateResult with scores."""
        embedding = MockEmbeddingService()
        nli = MockNLIService()
        service = SourceVerificationService(embedding, nli)

        claims = {"claim_a": "Work stress"}
        cr = service._retrieve_candidates_with_scores(claims, SEGMENTS)

        assert "claim_a" in cr.candidate_ids
        assert len(cr.candidate_ids["claim_a"]) <= 5
        assert "claim_a" in cr.embedding_scores
        # Should have a score for every segment
        assert len(cr.embedding_scores["claim_a"]) == len(SEGMENTS)
        assert len(cr.claim_embeddings) == 1
        assert len(cr.segment_embeddings) == len(SEGMENTS)


# ---------------------------------------------------------------------------
# Hybrid verification tests
# ---------------------------------------------------------------------------


class TestHybridVerifyPASSShortCircuit:
    """PASS from a primary signal short-circuits the chain."""

    def test_pass_stops_chain(self) -> None:
        tracker = _TrackingSignal()
        service, _nli = _make_service(
            primary_signals=[_PassSignal(confidence=0.85), tracker],
        )

        claims = {"claim_a": "Work stress"}
        attributions = {"claim_a": [1]}
        results = service.verify_attributions(claims, SEGMENTS, attributions)

        r = results[0]
        assert r.confidence_score == 0.85
        assert r.confidence_level == "medium"
        assert r.signal_used == "mock_pass"
        # Tracker should NOT have been called (PASS short-circuited)
        assert len(tracker.calls) == 0

    def test_pass_with_high_confidence(self) -> None:
        service, _nli = _make_service(
            primary_signals=[_PassSignal(confidence=0.92)],
        )

        claims = {"claim_a": "Work stress"}
        attributions = {"claim_a": [1]}
        results = service.verify_attributions(claims, SEGMENTS, attributions)

        assert results[0].confidence_level == "high"
        assert results[0].signal_used == "mock_pass"


class TestHybridVerifyFAILShortCircuit:
    """FAIL from a primary signal short-circuits the chain."""

    def test_fail_stops_chain(self) -> None:
        tracker = _TrackingSignal()
        service, _nli = _make_service(
            primary_signals=[_FailSignal(confidence=0.05), tracker],
        )

        claims = {"claim_a": "Work stress"}
        attributions = {"claim_a": [1]}
        results = service.verify_attributions(claims, SEGMENTS, attributions)

        r = results[0]
        assert r.confidence_score == 0.05
        assert r.confidence_level == "unverified"
        assert r.signal_used == "mock_fail"
        assert len(tracker.calls) == 0


class TestHybridVerifyUNCERTAINContinues:
    """UNCERTAIN lets the chain continue to the next signal."""

    def test_uncertain_continues_to_next(self) -> None:
        service, _nli = _make_service(
            primary_signals=[_UncertainSignal(), _PassSignal(confidence=0.80)],
        )

        claims = {"claim_a": "Work stress"}
        attributions = {"claim_a": [1]}
        results = service.verify_attributions(claims, SEGMENTS, attributions)

        r = results[0]
        assert r.confidence_score == 0.80
        assert r.signal_used == "mock_pass"

    def test_all_uncertain_returns_zero_confidence(self) -> None:
        service, _nli = _make_service(
            primary_signals=[_UncertainSignal(), _UncertainSignal()],
        )

        claims = {"claim_a": "Work stress"}
        attributions = {"claim_a": [1]}
        results = service.verify_attributions(claims, SEGMENTS, attributions)

        r = results[0]
        assert r.confidence_score == 0.0
        assert r.signal_used == "none"
        assert r.confidence_level == "unverified"


class TestSafetySignalOverride:
    """Safety signals can override a primary PASS verdict."""

    def test_safety_fail_overrides_primary_pass(self) -> None:
        service, _nli = _make_service(
            primary_signals=[_PassSignal(confidence=0.85)],
            safety_signals=[_FailSignal(confidence=0.05)],
        )

        claims = {"claim_a": "Client denies anxiety"}
        attributions = {"claim_a": [2]}
        results = service.verify_attributions(claims, SEGMENTS, attributions)

        r = results[0]
        # Safety signal FAIL should override the primary PASS
        assert r.confidence_score == 0.05
        assert r.signal_used == "mock_fail"
        assert r.confidence_level == "unverified"

    def test_safety_uncertain_does_not_override(self) -> None:
        service, _nli = _make_service(
            primary_signals=[_PassSignal(confidence=0.85)],
            safety_signals=[_UncertainSignal()],
        )

        claims = {"claim_a": "Work stress"}
        attributions = {"claim_a": [1]}
        results = service.verify_attributions(claims, SEGMENTS, attributions)

        r = results[0]
        # Safety UNCERTAIN should not override primary PASS
        assert r.confidence_score == 0.85
        assert r.signal_used == "mock_pass"

    def test_safety_runs_after_primary_fail(self) -> None:
        """Safety signals still run even when primary returned FAIL."""
        safety_tracker = _TrackingSignal()
        service, _nli = _make_service(
            primary_signals=[_FailSignal(confidence=0.05)],
            safety_signals=[safety_tracker],
        )

        claims = {"claim_a": "Work stress"}
        attributions = {"claim_a": [1]}
        service.verify_attributions(claims, SEGMENTS, attributions)

        # Safety signal should have been called even after primary FAIL
        assert len(safety_tracker.calls) == 1


class TestHybridVerifyMultipleSegments:
    """Hybrid verify picks best confidence across attributed segments."""

    def test_best_confidence_across_segments(self) -> None:
        """When multiple segments are attributed, best confidence wins."""

        class _SegmentAwareSignal(VerificationSignal):
            """Returns different confidence based on segment text."""

            @property
            def name(self) -> str:
                return "segment_aware"

            def check(
                self,
                _claim_text: str,
                segment_text: str,
                _context: SignalContext,
            ) -> SignalResult:
                if "stressful" in segment_text:
                    return SignalResult(
                        SignalVerdict.PASS,
                        0.90,
                        self.name,
                        "High match",
                    )
                return SignalResult(
                    SignalVerdict.PASS,
                    0.40,
                    self.name,
                    "Low match",
                )

        service, _nli = _make_service(
            primary_signals=[_SegmentAwareSignal()],
        )

        claims = {"claim_a": "Work stress"}
        # Segment 0 = "How have you been feeling?" (low match)
        # Segment 1 = "Work has been really stressful." (high match)
        attributions = {"claim_a": [0, 1]}
        results = service.verify_attributions(claims, SEGMENTS, attributions)

        r = results[0]
        assert r.confidence_score == 0.90
        assert r.signal_used == "segment_aware"


class TestHybridVerifyNoAttributions:
    """Claims with no attributions get unverified in hybrid mode."""

    def test_no_attributions_unverified(self) -> None:
        service, _nli = _make_service(
            primary_signals=[_PassSignal(confidence=0.85)],
        )

        claims = {"claim_a": "Follow up in one week."}
        attributions: dict[str, list[int]] = {"claim_a": []}
        results = service.verify_attributions(claims, SEGMENTS, attributions)

        r = results[0]
        assert r.confidence_level == "unverified"
        assert r.confidence_score == 0.0
        assert r.signal_used == ""


class TestSignalUsedField:
    """signal_used is populated correctly in various scenarios."""

    def test_nli_fallback_sets_signal_used(self) -> None:
        """NLI fallback path sets signal_used to 'nli'."""
        service, _nli = _make_service(default_label="entailment", default_score=0.92)
        claims = {"claim_a": "Work stress"}
        attributions = {"claim_a": [1]}
        results = service.verify_attributions(claims, SEGMENTS, attributions)
        assert results[0].signal_used == "nli"

    def test_hybrid_sets_signal_name(self) -> None:
        service, _nli = _make_service(
            primary_signals=[_PassSignal(confidence=0.85)],
        )
        claims = {"claim_a": "Work stress"}
        attributions = {"claim_a": [1]}
        results = service.verify_attributions(claims, SEGMENTS, attributions)
        assert results[0].signal_used == "mock_pass"

    def test_safety_override_records_safety_signal(self) -> None:
        service, _nli = _make_service(
            primary_signals=[_PassSignal(confidence=0.85)],
            safety_signals=[_FailSignal(confidence=0.05)],
        )
        claims = {"claim_a": "Work stress"}
        attributions = {"claim_a": [1]}
        results = service.verify_attributions(claims, SEGMENTS, attributions)
        assert results[0].signal_used == "mock_fail"
