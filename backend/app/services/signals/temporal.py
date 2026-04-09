# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Temporal consistency safety signal for the hybrid verification pipeline.

Detects temporal mismatches between claim and segment text. Catches errors
like attributing past-tense claims to present-tense segments, or mismatched
durations and frequencies.

This signal NEVER returns PASS -- only FAIL (temporal mismatch) or UNCERTAIN.
FAIL verdicts have low confidence (0.10-0.15) because tense detection in
informal speech is unreliable.
"""

from __future__ import annotations

import re

from app.services.verification_signals import (
    SignalContext,
    SignalResult,
    SignalVerdict,
    VerificationSignal,
)

DOMINANCE_FACTOR = 2  # Tense must have 2x more matches than runner-up
COMPATIBILITY_TOLERANCE = 1.2  # 20% tolerance for duration/frequency comparison

# --- Temporal patterns ---

PAST_PATTERNS: list[re.Pattern[str]] = [
    re.compile(
        r"\b(last|previous|previously|ago|formerly|used to|had been|was|were"
        r"|reported|experienced|felt|mentioned|described|indicated|stated|noted"
        r"|had|did|went|came|took|began|started)\b",
        re.IGNORECASE,
    ),
]

PRESENT_PATTERNS: list[re.Pattern[str]] = [
    re.compile(
        r"\b(currently|now|is|are|has been|ongoing|present|today"
        r"|reports|states|describes|continues"
        r"|remains|maintains|endorses|exhibits)\b",
        re.IGNORECASE,
    ),
]

FUTURE_PATTERNS: list[re.Pattern[str]] = [
    re.compile(
        r"\b(will|plan|going to|intend|schedule|next|upcoming"
        r"|plans to|intends to|aims to|hopes to|goals? include)\b",
        re.IGNORECASE,
    ),
]

DURATION_PATTERN = re.compile(
    r"\b(\d+)\s*(days?|weeks?|months?|years?)\b",
    re.IGNORECASE,
)

FREQUENCY_PATTERN = re.compile(
    r"\b(\d+)\s*(?:times?|x|episodes?)\s*(?:per|a|/)\s*(day|week|month|year)\b",
    re.IGNORECASE,
)

FREQUENCY_WORDS: dict[str, tuple[int, str]] = {
    "daily": (1, "day"),
    "twice daily": (2, "day"),
    "weekly": (1, "week"),
    "twice weekly": (2, "week"),
    "monthly": (1, "month"),
    "once daily": (1, "day"),
    "once weekly": (1, "week"),
    "once monthly": (1, "month"),
    "once a day": (1, "day"),
    "once a week": (1, "week"),
    "once a month": (1, "month"),
    "twice a day": (2, "day"),
    "twice a week": (2, "week"),
    "three times a day": (3, "day"),
    "three times a week": (3, "week"),
}

# Conversion to days for duration comparison
UNIT_TO_DAYS: dict[str, float] = {
    "day": 1,
    "days": 1,
    "week": 7,
    "weeks": 7,
    "month": 30,
    "months": 30,
    "year": 365,
    "years": 365,
}


def _detect_tense(text: str) -> str | None:
    """Detect dominant tense in text. Returns 'past', 'present', 'future', or None."""
    scores: dict[str, int] = {"past": 0, "present": 0, "future": 0}

    for pattern in PAST_PATTERNS:
        scores["past"] += len(pattern.findall(text))

    for pattern in PRESENT_PATTERNS:
        scores["present"] += len(pattern.findall(text))

    for pattern in FUTURE_PATTERNS:
        scores["future"] += len(pattern.findall(text))

    max_score = max(scores.values())
    if max_score == 0:
        return None

    # Only return a tense if it clearly dominates (at least 2x the runner-up)
    sorted_tenses = sorted(scores.items(), key=lambda x: x[1], reverse=True)
    if (
        len(sorted_tenses) >= DOMINANCE_FACTOR
        and sorted_tenses[0][1] > 0
        and (
            sorted_tenses[1][1] == 0
            or sorted_tenses[0][1] >= DOMINANCE_FACTOR * sorted_tenses[1][1]
        )
    ):
        return sorted_tenses[0][0]

    return None


def _extract_durations(text: str) -> list[tuple[int, str]]:
    """Extract duration expressions as (count, unit) tuples."""
    return [(int(m.group(1)), m.group(2).lower()) for m in DURATION_PATTERN.finditer(text)]


def _extract_frequencies(text: str) -> list[tuple[int, str]]:
    """Extract frequency expressions as (count, period) tuples."""
    freqs: list[tuple[int, str]] = []

    for match in FREQUENCY_PATTERN.finditer(text):
        freqs.append((int(match.group(1)), match.group(2).lower()))

    lower = text.lower()
    for phrase, (count, period) in FREQUENCY_WORDS.items():
        if phrase in lower:
            freqs.append((count, period))

    return freqs


def _durations_compatible(
    claim_durations: list[tuple[int, str]],
    segment_durations: list[tuple[int, str]],
) -> bool:
    """Check if any duration pair across claim and segment is compatible.

    Two durations are compatible if they convert to roughly the same
    number of days (within 20% tolerance).
    """
    for c_count, c_unit in claim_durations:
        c_days = c_count * UNIT_TO_DAYS.get(c_unit, 0)
        for s_count, s_unit in segment_durations:
            s_days = s_count * UNIT_TO_DAYS.get(s_unit, 0)
            if c_days > 0 and s_days > 0:
                ratio = max(c_days, s_days) / min(c_days, s_days)
                if ratio <= COMPATIBILITY_TOLERANCE:
                    return True
    return False


def _frequencies_compatible(
    claim_freqs: list[tuple[int, str]],
    segment_freqs: list[tuple[int, str]],
) -> bool:
    """Check if any frequency pair across claim and segment is compatible.

    Two frequencies are compatible if they represent the same rate
    (normalized to per-day).
    """

    def _to_per_day(count: int, period: str) -> float:
        days = UNIT_TO_DAYS.get(period, 0)
        return count / days if days > 0 else 0

    for c_count, c_period in claim_freqs:
        c_rate = _to_per_day(c_count, c_period)
        for s_count, s_period in segment_freqs:
            s_rate = _to_per_day(s_count, s_period)
            if c_rate > 0 and s_rate > 0:
                ratio = max(c_rate, s_rate) / min(c_rate, s_rate)
                if ratio <= COMPATIBILITY_TOLERANCE:
                    return True
    return False


class TemporalConsistencySignal(VerificationSignal):
    """Detect temporal mismatches between claim and segment.

    This is a safety signal: it NEVER returns PASS. It returns FAIL when
    a temporal mismatch is detected, and UNCERTAIN otherwise. FAIL verdicts
    have low confidence (0.10-0.15) because tense detection in informal
    speech is unreliable.
    """

    @property
    def name(self) -> str:
        return "temporal"

    def check(
        self,
        claim_text: str,
        segment_text: str,
        context: SignalContext,  # noqa: ARG002
    ) -> SignalResult:
        # Check tense mismatch (past vs present is the dangerous case)
        claim_tense = _detect_tense(claim_text)
        segment_tense = _detect_tense(segment_text)

        # Only flag tense mismatch if the SEGMENT is present and the
        # CLAIM is past -- that would indicate attributing past events
        # to something stated as currently happening.
        # The reverse (claim=present, segment=past) is NORMAL in clinical
        # documentation: "Client reports X" (present) about past events.
        if (
            claim_tense
            and segment_tense
            and claim_tense != segment_tense
            and {claim_tense, segment_tense} == {"past", "present"}
            and claim_tense == "past"  # only flag if claim is past, segment is present
        ):
            return SignalResult(
                verdict=SignalVerdict.FAIL,
                confidence=0.15,
                signal_name=self.name,
                detail=(f"Tense mismatch: claim={claim_tense}, segment={segment_tense}"),
            )

        # Check duration mismatch
        claim_durations = _extract_durations(claim_text)
        segment_durations = _extract_durations(segment_text)
        if (
            claim_durations
            and segment_durations
            and not _durations_compatible(claim_durations, segment_durations)
        ):
            return SignalResult(
                verdict=SignalVerdict.FAIL,
                confidence=0.10,
                signal_name=self.name,
                detail=(f"Duration mismatch: claim={claim_durations}, segment={segment_durations}"),
            )

        # Check frequency mismatch
        claim_freqs = _extract_frequencies(claim_text)
        segment_freqs = _extract_frequencies(segment_text)
        if (
            claim_freqs
            and segment_freqs
            and not _frequencies_compatible(claim_freqs, segment_freqs)
        ):
            return SignalResult(
                verdict=SignalVerdict.FAIL,
                confidence=0.10,
                signal_name=self.name,
                detail=(f"Frequency mismatch: claim={claim_freqs}, segment={segment_freqs}"),
            )

        return SignalResult(
            verdict=SignalVerdict.UNCERTAIN,
            confidence=0.5,
            signal_name=self.name,
            detail="No temporal mismatch detected",
        )
