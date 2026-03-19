# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Tests for the negation detector signal (S1 -- safety signal)."""

import os

os.environ["ENVIRONMENT"] = "development"

from app.services.signals.negation import (
    NEGATION_CUES,
    THERAPY_NEGATION_TERMS,
    NegationSignal,
    _content_matches,
    _find_negation_cue,
    _strip_negation,
)
from app.services.verification_signals import SignalContext, SignalVerdict


def _make_context() -> SignalContext:
    return SignalContext(claim_key="test_claim")


class TestNegationCueDictionaries:
    """Verify the cue dictionaries are well-formed."""

    def test_negation_cues_has_three_categories(self):
        assert set(NEGATION_CUES.keys()) == {"explicit", "clinical", "cessation"}

    def test_all_categories_are_nonempty(self):
        for category, cues in NEGATION_CUES.items():
            assert len(cues) > 0, f"Category '{category}' is empty"

    def test_therapy_negation_terms_nonempty(self):
        assert len(THERAPY_NEGATION_TERMS) > 0


class TestRegexHelpers:
    """Unit tests for the regex-level helper functions."""

    def test_find_negation_cue_explicit_excluded(self):
        """Explicit cues (not, no, etc.) are excluded from regex tier to
        avoid false positives in conversational speech like 'Not great'."""
        found, _cue = _find_negation_cue("patient does not report anxiety")
        assert found is False  # "not " excluded from regex tier

    def test_find_negation_cue_clinical(self):
        found, cue = _find_negation_cue("denies suicidal ideation")
        assert found is True
        assert cue == "denies"

    def test_find_negation_cue_cessation(self):
        found, cue = _find_negation_cue("stopped taking medication")
        assert found is True
        assert cue == "stopped"

    def test_find_negation_cue_none(self):
        found, cue = _find_negation_cue("reports feeling anxious")
        assert found is False
        assert cue == ""

    def test_strip_negation_removes_cue(self):
        result = _strip_negation("denies anxiety symptoms", "denies")
        assert result == "anxiety symptoms"

    def test_strip_negation_empty_cue(self):
        result = _strip_negation("some text", "")
        assert result == "some text"

    def test_content_matches_overlapping(self):
        assert _content_matches("anxiety symptoms", "anxiety symptoms") is True

    def test_content_matches_partial_overlap(self):
        assert _content_matches("anxiety symptoms today", "anxiety") is True

    def test_content_matches_no_overlap(self):
        assert _content_matches("headache", "anxiety") is False


class TestNegationSignalPolarityFlip:
    """Test the core polarity flip detection."""

    def test_explicit_negation_denies_vs_reports(self):
        """'denies anxiety' vs 'reports anxiety' should FAIL (polarity flip)."""
        signal = NegationSignal()
        result = signal.check("denies anxiety", "reports anxiety", _make_context())
        assert result.verdict == SignalVerdict.FAIL
        assert result.confidence == 0.05
        assert result.signal_name == "negation"
        assert "Polarity flip" in result.detail

    def test_cessation_stopped_vs_taking(self):
        """'stopped taking medication' vs 'taking medication' should FAIL."""
        signal = NegationSignal()
        result = signal.check(
            "stopped taking medication",
            "taking medication regularly",
            _make_context(),
        )
        assert result.verdict == SignalVerdict.FAIL
        assert result.confidence == 0.05

    def test_explicit_not_vs_affirmative(self):
        """'does not endorse suicidal ideation' vs 'endorses suicidal ideation'."""
        signal = NegationSignal()
        result = signal.check(
            "does not endorse suicidal ideation",
            "endorses suicidal ideation",
            _make_context(),
        )
        assert result.verdict == SignalVerdict.FAIL

    def test_clinical_no_evidence_vs_evidence(self):
        """'no evidence of depression' vs 'shows evidence of depression'."""
        signal = NegationSignal()
        result = signal.check(
            "no evidence of depression",
            "shows evidence of depression",
            _make_context(),
        )
        assert result.verdict == SignalVerdict.FAIL


class TestNegationSignalUncertain:
    """Cases where negation signal should return UNCERTAIN."""

    def test_both_positive_returns_uncertain(self):
        """Both texts positive (no negation) should be UNCERTAIN."""
        signal = NegationSignal()
        result = signal.check("reports anxiety", "feeling anxious", _make_context())
        assert result.verdict == SignalVerdict.UNCERTAIN
        assert result.confidence == 0.5

    def test_both_negated_returns_uncertain(self):
        """Both texts negated should be UNCERTAIN (not a flip)."""
        signal = NegationSignal()
        result = signal.check(
            "denies suicidal ideation",
            "never had suicidal thoughts",
            _make_context(),
        )
        assert result.verdict == SignalVerdict.UNCERTAIN

    def test_negation_no_content_overlap_returns_uncertain(self):
        """Negation in one side but different content should be UNCERTAIN."""
        signal = NegationSignal()
        result = signal.check(
            "denies headache",
            "reports anxiety symptoms",
            _make_context(),
        )
        assert result.verdict == SignalVerdict.UNCERTAIN

    def test_plain_text_no_negation(self):
        """Plain text without any negation cues."""
        signal = NegationSignal()
        result = signal.check(
            "Client discussed work stress",
            "Work has been overwhelming lately",
            _make_context(),
        )
        assert result.verdict == SignalVerdict.UNCERTAIN
        assert result.confidence == 0.5


class TestNegationSignalNeverPasses:
    """Verify the signal never returns PASS."""

    def test_matching_text_is_uncertain_not_pass(self):
        """Even perfectly matching text should be UNCERTAIN, never PASS."""
        signal = NegationSignal()
        result = signal.check("anxiety disorder", "anxiety disorder", _make_context())
        assert result.verdict == SignalVerdict.UNCERTAIN

    def test_signal_name(self):
        signal = NegationSignal()
        assert signal.name == "negation"
