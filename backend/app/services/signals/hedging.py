# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Signal 3: Hedging/certainty detector.

Detects qualifier mismatches between claim and segment text. Catches patterns
like "somewhat anxious" vs "severe anxiety" where token overlap would pass
but the clinical meaning is significantly different.

Qualifier categories:
  - frequency: low/medium/high (e.g. "occasional" vs "chronic")
  - severity: low/medium/high (e.g. "mild" vs "severe")
  - certainty: reported/denied/confirmed (informational, not scored)

This signal NEVER returns PASS. It can FAIL on a two-level qualifier jump,
return UNCERTAIN with low confidence (0.3) for one-level differences,
or return UNCERTAIN with normal confidence (0.5) when no qualifier mismatch.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from app.services.verification_signals import SignalResult, SignalVerdict, VerificationSignal

if TYPE_CHECKING:
    from app.services.verification_signals import SignalContext

# ---------------------------------------------------------------------------
# Qualifier dictionaries
# ---------------------------------------------------------------------------

QUALIFIERS: dict[str, dict[str, list[str]]] = {
    "frequency": {
        "low": ["occasional", "sometimes", "rarely", "infrequent", "sporadic"],
        "medium": ["often", "frequent", "regular", "several"],
        "high": ["constant", "chronic", "persistent", "always", "daily", "continuous"],
    },
    "severity": {
        "low": ["mild", "slight", "minor", "somewhat", "a little", "a bit"],
        "medium": ["moderate", "significant", "notable", "considerable"],
        "high": ["severe", "extreme", "intense", "debilitating", "overwhelming", "acute"],
    },
    "certainty": {
        "reported": ["reports", "states", "says", "describes", "mentions", "endorses"],
        "denied": ["denies", "denied", "does not report", "does not endorse"],
        "confirmed": ["confirms", "verified", "demonstrated", "observed", "assessed"],
    },
}

# Ordered levels for distance calculation (frequency and severity only)
_ORDERED_LEVELS = ["low", "medium", "high"]

# Pre-compile lookup: term -> (category, level)
_TERM_LOOKUP: dict[str, tuple[str, str]] = {}
for _cat, _levels in QUALIFIERS.items():
    for _level, _terms in _levels.items():
        for _term in _terms:
            _TERM_LOOKUP[_term.lower()] = (_cat, _level)

# Sort terms by length (longest first) so multi-word terms match before single words
_SORTED_TERMS = sorted(_TERM_LOOKUP.keys(), key=len, reverse=True)

# Confidence for a two-level qualifier jump (FAIL)
_FAIL_CONFIDENCE = 0.15

# Confidence for a one-level qualifier difference (UNCERTAIN)
_ONE_LEVEL_CONFIDENCE = 0.3

# Confidence when no qualifier mismatch detected (UNCERTAIN)
_NO_MISMATCH_CONFIDENCE = 0.5

# Minimum level distance to be considered a significant mismatch (FAIL)
_TWO_LEVEL_JUMP = 2


class HedgingSignal(VerificationSignal):
    """Detect qualifier/certainty mismatches between claim and segment.

    Never returns PASS -- only FAIL or UNCERTAIN.
    """

    @property
    def name(self) -> str:
        return "hedging"

    def check(
        self,
        claim_text: str,
        segment_text: str,
        context: SignalContext,  # noqa: ARG002
    ) -> SignalResult:
        claim_quals = _extract_qualifiers(claim_text)
        segment_quals = _extract_qualifiers(segment_text)

        # Check frequency and severity categories for mismatches
        for category in ("frequency", "severity"):
            claim_level = claim_quals.get(category)
            segment_level = segment_quals.get(category)

            if claim_level and segment_level and claim_level != segment_level:
                distance = abs(
                    _ORDERED_LEVELS.index(claim_level) - _ORDERED_LEVELS.index(segment_level)
                )
                if distance >= _TWO_LEVEL_JUMP:
                    return SignalResult(
                        verdict=SignalVerdict.FAIL,
                        confidence=_FAIL_CONFIDENCE,
                        signal_name=self.name,
                        detail=(
                            f"{category} mismatch: claim={claim_level}, segment={segment_level}"
                        ),
                    )
                # One-level difference: uncertain but notable
                return SignalResult(
                    verdict=SignalVerdict.UNCERTAIN,
                    confidence=_ONE_LEVEL_CONFIDENCE,
                    signal_name=self.name,
                    detail=(
                        f"{category} slight mismatch: claim={claim_level}, segment={segment_level}"
                    ),
                )

        return SignalResult(
            verdict=SignalVerdict.UNCERTAIN,
            confidence=_NO_MISMATCH_CONFIDENCE,
            signal_name=self.name,
            detail="No qualifier mismatch",
        )


# ---------------------------------------------------------------------------
# Qualifier extraction
# ---------------------------------------------------------------------------


def _extract_qualifiers(text: str) -> dict[str, str]:
    """Extract qualifiers from text, returning {category: level} for each found."""
    text_lower = text.lower()
    found: dict[str, str] = {}

    for term in _SORTED_TERMS:
        # Use word boundary matching for single-word terms; substring for multi-word
        if " " in term:
            if term in text_lower:
                cat, level = _TERM_LOOKUP[term]
                if cat not in found:
                    found[cat] = level
        elif re.search(rf"\b{re.escape(term)}\b", text_lower):
            cat, level = _TERM_LOOKUP[term]
            if cat not in found:
                found[cat] = level

    return found
