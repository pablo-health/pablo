# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Tests for the temporal consistency safety signal."""

import os

os.environ["ENVIRONMENT"] = "development"

import pytest
from app.services.signals.temporal import (
    TemporalConsistencySignal,
    _detect_tense,
    _durations_compatible,
    _extract_durations,
    _extract_frequencies,
    _frequencies_compatible,
)
from app.services.verification_signals import SignalContext, SignalVerdict


@pytest.fixture
def signal() -> TemporalConsistencySignal:
    return TemporalConsistencySignal()


@pytest.fixture
def ctx() -> SignalContext:
    return SignalContext(claim_key="test.claim")


class TestTemporalConsistencyName:
    def test_name(self, signal: TemporalConsistencySignal) -> None:
        assert signal.name == "temporal"


class TestTemporalConsistencyFAIL:
    """Temporal mismatches -> FAIL."""

    def test_tense_mismatch_past_vs_present(
        self, signal: TemporalConsistencySignal, ctx: SignalContext
    ) -> None:
        result = signal.check(
            "Client was experiencing panic attacks last week",
            "I am currently experiencing panic attacks today",
            ctx,
        )
        assert result.verdict == SignalVerdict.FAIL
        assert result.confidence == 0.15
        assert "tense" in result.detail.lower()

    def test_duration_mismatch(self, signal: TemporalConsistencySignal, ctx: SignalContext) -> None:
        result = signal.check(
            "Symptoms persisting for 3 weeks",
            "I've had this for 2 months now",
            ctx,
        )
        assert result.verdict == SignalVerdict.FAIL
        assert result.confidence == 0.10
        assert "duration" in result.detail.lower()

    def test_frequency_mismatch(
        self, signal: TemporalConsistencySignal, ctx: SignalContext
    ) -> None:
        result = signal.check(
            "Panic attacks occur twice daily",
            "I get them once weekly",
            ctx,
        )
        assert result.verdict == SignalVerdict.FAIL
        assert result.confidence == 0.10
        assert "frequenc" in result.detail.lower()

    def test_low_confidence_on_tense_fail(
        self, signal: TemporalConsistencySignal, ctx: SignalContext
    ) -> None:
        result = signal.check(
            "Client was feeling depressed previously",
            "I am currently feeling very depressed today",
            ctx,
        )
        assert result.verdict == SignalVerdict.FAIL
        assert result.confidence <= 0.15


class TestTemporalConsistencyUNCERTAIN:
    """Matching or absent temporal info -> UNCERTAIN."""

    def test_matching_tense(self, signal: TemporalConsistencySignal, ctx: SignalContext) -> None:
        result = signal.check(
            "Client reported feeling anxious last week",
            "I was really anxious last week",
            ctx,
        )
        assert result.verdict == SignalVerdict.UNCERTAIN
        assert result.confidence == 0.5

    def test_no_temporal_info(self, signal: TemporalConsistencySignal, ctx: SignalContext) -> None:
        result = signal.check(
            "Sweating",
            "I noticed some sweating",
            ctx,
        )
        assert result.verdict == SignalVerdict.UNCERTAIN
        assert result.confidence == 0.5

    def test_future_vs_past_not_flagged(
        self, signal: TemporalConsistencySignal, ctx: SignalContext
    ) -> None:
        """Future vs past is not the dangerous past/present case."""
        result = signal.check(
            "Will schedule next appointment upcoming week",
            "I was doing well previously",
            ctx,
        )
        assert result.verdict == SignalVerdict.UNCERTAIN

    def test_compatible_durations(
        self, signal: TemporalConsistencySignal, ctx: SignalContext
    ) -> None:
        result = signal.check(
            "Symptoms for 4 weeks",
            "About 1 month of symptoms",
            ctx,
        )
        assert result.verdict == SignalVerdict.UNCERTAIN


class TestTemporalConsistencyNeverPASS:
    """Safety signal must never return PASS."""

    def test_perfect_temporal_match_still_uncertain(
        self, signal: TemporalConsistencySignal, ctx: SignalContext
    ) -> None:
        result = signal.check(
            "Was experiencing anxiety for 3 weeks, reported 2 times a day",
            "I was anxious for 3 weeks, happening 2 times a day",
            ctx,
        )
        assert result.verdict != SignalVerdict.PASS

    def test_no_temporal_info_never_pass(
        self, signal: TemporalConsistencySignal, ctx: SignalContext
    ) -> None:
        result = signal.check("Sweating", "I was sweating", ctx)
        assert result.verdict != SignalVerdict.PASS


class TestTenseDetection:
    """Tests for _detect_tense helper."""

    def test_detects_past_tense(self) -> None:
        assert _detect_tense("Client was experiencing symptoms last week") == "past"

    def test_detects_present_tense(self) -> None:
        assert _detect_tense("Client is currently experiencing anxiety today") == "present"

    def test_detects_future_tense(self) -> None:
        assert _detect_tense("Will schedule next appointment upcoming week") == "future"

    def test_returns_none_for_no_temporal(self) -> None:
        assert _detect_tense("Sweating") is None

    def test_requires_dominant_tense(self) -> None:
        """Ambiguous text with equal past/present markers returns None."""
        assert _detect_tense("was is") is None


class TestDurationExtraction:
    """Tests for _extract_durations helper."""

    def test_extracts_weeks(self) -> None:
        durations = _extract_durations("for 3 weeks")
        assert (3, "weeks") in durations

    def test_extracts_months(self) -> None:
        durations = _extract_durations("about 2 months")
        assert (2, "months") in durations

    def test_extracts_multiple(self) -> None:
        durations = _extract_durations("3 weeks and then 2 months")
        assert len(durations) == 2

    def test_no_durations(self) -> None:
        assert _extract_durations("feeling better") == []


class TestDurationCompatibility:
    """Tests for _durations_compatible helper."""

    def test_same_duration_compatible(self) -> None:
        assert _durations_compatible([(3, "weeks")], [(3, "weeks")])

    def test_equivalent_durations_compatible(self) -> None:
        # 4 weeks (28 days) ~ 1 month (30 days), within 20% tolerance
        assert _durations_compatible([(4, "weeks")], [(1, "month")])

    def test_different_durations_incompatible(self) -> None:
        assert not _durations_compatible([(3, "weeks")], [(2, "months")])

    def test_very_different_durations_incompatible(self) -> None:
        assert not _durations_compatible([(1, "week")], [(1, "year")])


class TestFrequencyExtraction:
    """Tests for _extract_frequencies helper."""

    def test_extracts_numeric_frequency(self) -> None:
        freqs = _extract_frequencies("2 times a day")
        assert (2, "day") in freqs

    def test_extracts_word_frequency(self) -> None:
        freqs = _extract_frequencies("Twice daily sessions")
        assert (2, "day") in freqs

    def test_no_frequency(self) -> None:
        assert _extract_frequencies("general anxiety") == []


class TestFrequencyCompatibility:
    """Tests for _frequencies_compatible helper."""

    def test_same_frequency_compatible(self) -> None:
        assert _frequencies_compatible([(2, "day")], [(2, "day")])

    def test_different_frequency_incompatible(self) -> None:
        assert not _frequencies_compatible([(2, "day")], [(1, "week")])

    def test_equivalent_frequency_compatible(self) -> None:
        # 7 times a week = 1 time a day
        assert _frequencies_compatible([(7, "week")], [(1, "day")])
