# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Tests for the hedging/certainty detector signal (Signal 3)."""

import os

os.environ["ENVIRONMENT"] = "development"

from app.services.signals.hedging import (
    QUALIFIERS,
    HedgingSignal,
    _extract_qualifiers,
)
from app.services.verification_signals import SignalContext, SignalVerdict


def _make_context() -> SignalContext:
    return SignalContext(claim_key="test_claim")


class TestQualifierDictionaries:
    """Verify qualifier dictionaries are well-formed."""

    def test_has_three_categories(self):
        assert set(QUALIFIERS.keys()) == {"frequency", "severity", "certainty"}

    def test_frequency_has_three_levels(self):
        assert set(QUALIFIERS["frequency"].keys()) == {"low", "medium", "high"}

    def test_severity_has_three_levels(self):
        assert set(QUALIFIERS["severity"].keys()) == {"low", "medium", "high"}

    def test_certainty_has_three_levels(self):
        assert set(QUALIFIERS["certainty"].keys()) == {"reported", "denied", "confirmed"}

    def test_all_levels_nonempty(self):
        for cat, levels in QUALIFIERS.items():
            for level, terms in levels.items():
                assert len(terms) > 0, f"{cat}.{level} is empty"


class TestQualifierExtraction:
    """Test the _extract_qualifiers helper."""

    def test_extracts_severity_low(self):
        result = _extract_qualifiers("mild anxiety symptoms")
        assert result.get("severity") == "low"

    def test_extracts_severity_high(self):
        result = _extract_qualifiers("severe depression noted")
        assert result.get("severity") == "high"

    def test_extracts_frequency_low(self):
        result = _extract_qualifiers("occasional panic attacks")
        assert result.get("frequency") == "low"

    def test_extracts_frequency_high(self):
        result = _extract_qualifiers("chronic insomnia reported")
        assert result.get("frequency") == "high"

    def test_no_qualifiers_found(self):
        result = _extract_qualifiers("anxiety symptoms")
        assert result == {}

    def test_multi_word_qualifier(self):
        result = _extract_qualifiers("reports a little anxiety")
        assert result.get("severity") == "low"

    def test_multiple_categories(self):
        result = _extract_qualifiers("chronic severe headaches")
        assert result.get("frequency") == "high"
        assert result.get("severity") == "high"

    def test_case_insensitive(self):
        result = _extract_qualifiers("SEVERE anxiety")
        assert result.get("severity") == "high"


class TestHedgingSeverityMismatch:
    """Test severity mismatch detection."""

    def test_mild_vs_severe_fails(self):
        """Two-level jump (low -> high) should FAIL."""
        signal = HedgingSignal()
        result = signal.check("mild anxiety", "severe anxiety", _make_context())
        assert result.verdict == SignalVerdict.FAIL
        assert result.confidence == 0.15
        assert result.signal_name == "hedging"
        assert "severity mismatch" in result.detail
        assert "claim=low" in result.detail
        assert "segment=high" in result.detail

    def test_severe_vs_mild_fails(self):
        """Two-level jump (high -> low) should also FAIL."""
        signal = HedgingSignal()
        result = signal.check("severe anxiety", "mild anxiety", _make_context())
        assert result.verdict == SignalVerdict.FAIL

    def test_mild_vs_moderate_uncertain(self):
        """One-level difference (low -> medium) should be UNCERTAIN with low confidence."""
        signal = HedgingSignal()
        result = signal.check("mild anxiety", "moderate anxiety", _make_context())
        assert result.verdict == SignalVerdict.UNCERTAIN
        assert result.confidence == 0.3
        assert "slight mismatch" in result.detail

    def test_moderate_vs_severe_uncertain(self):
        """One-level difference (medium -> high) should be UNCERTAIN with low confidence."""
        signal = HedgingSignal()
        result = signal.check("moderate depression", "severe depression", _make_context())
        assert result.verdict == SignalVerdict.UNCERTAIN
        assert result.confidence == 0.3


class TestHedgingFrequencyMismatch:
    """Test frequency mismatch detection."""

    def test_occasional_vs_chronic_fails(self):
        """Two-level jump (low -> high) should FAIL."""
        signal = HedgingSignal()
        result = signal.check("occasional headaches", "chronic headaches", _make_context())
        assert result.verdict == SignalVerdict.FAIL
        assert "frequency mismatch" in result.detail

    def test_sometimes_vs_always_fails(self):
        """Two-level jump in frequency should FAIL."""
        signal = HedgingSignal()
        result = signal.check("sometimes feels anxious", "always feels anxious", _make_context())
        assert result.verdict == SignalVerdict.FAIL

    def test_occasional_vs_frequent_uncertain(self):
        """One-level frequency difference should be UNCERTAIN."""
        signal = HedgingSignal()
        result = signal.check("occasional insomnia", "frequent insomnia", _make_context())
        assert result.verdict == SignalVerdict.UNCERTAIN
        assert result.confidence == 0.3


class TestHedgingNoMismatch:
    """Cases where hedging signal should return UNCERTAIN with normal confidence."""

    def test_no_qualifiers_in_either(self):
        """Plain text without qualifiers should be UNCERTAIN (0.5)."""
        signal = HedgingSignal()
        result = signal.check("anxiety symptoms", "feeling anxious", _make_context())
        assert result.verdict == SignalVerdict.UNCERTAIN
        assert result.confidence == 0.5
        assert "No qualifier mismatch" in result.detail

    def test_matching_qualifiers_both_severe(self):
        """Both texts with same qualifier should be UNCERTAIN (0.5)."""
        signal = HedgingSignal()
        result = signal.check("severe anxiety", "severe anxiety noted", _make_context())
        assert result.verdict == SignalVerdict.UNCERTAIN
        assert result.confidence == 0.5

    def test_matching_qualifiers_both_mild(self):
        """Both texts with same qualifier should be UNCERTAIN (0.5)."""
        signal = HedgingSignal()
        result = signal.check("mild headache", "slight headache", _make_context())
        assert result.verdict == SignalVerdict.UNCERTAIN
        assert result.confidence == 0.5

    def test_qualifier_only_in_one_text(self):
        """Qualifier in one text but not the other -- no mismatch possible."""
        signal = HedgingSignal()
        result = signal.check("severe anxiety", "has anxiety", _make_context())
        assert result.verdict == SignalVerdict.UNCERTAIN
        assert result.confidence == 0.5


class TestHedgingNeverPasses:
    """Verify the signal never returns PASS."""

    def test_identical_text_is_uncertain_not_pass(self):
        signal = HedgingSignal()
        result = signal.check("severe anxiety", "severe anxiety", _make_context())
        assert result.verdict != SignalVerdict.PASS

    def test_signal_name(self):
        signal = HedgingSignal()
        assert signal.name == "hedging"
