# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Signal S1: Negation detector (safety signal).

Detects polarity flips between claim and segment text. This is the most
clinically dangerous error type -- e.g. "Client denies suicidal ideation"
vs "Client reported suicidal ideation".

Two-tier implementation:
  Tier 1: Regex fast path (~0.1ms) using curated negation cue lists
  Tier 2: negspacy NegEx fallback (~5ms) for implicit negation patterns

This signal NEVER returns PASS. It can only FAIL (polarity flip detected)
or remain UNCERTAIN (no issue found).
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

from app.services.verification_signals import SignalResult, SignalVerdict, VerificationSignal

if TYPE_CHECKING:
    from app.services.verification_signals import SignalContext

# ---------------------------------------------------------------------------
# Negation cue dictionaries
# ---------------------------------------------------------------------------

NEGATION_CUES: dict[str, list[str]] = {
    "explicit": ["no ", "not ", "n't ", "never ", "neither ", "nor "],
    "clinical": [
        "denies",
        "denied",
        "denying",
        "without",
        "absent",
        "negative for",
        "rules out",
        "ruled out",
        "no evidence of",
        "no history of",
        "no signs of",
        "no symptoms of",
        "does not endorse",
        "did not endorse",
        "no complaints of",
        "no reports of",
        "absence of",
        "no longer experiencing",
    ],
    "cessation": [
        "stopped",
        "quit",
        "quitting",
        "ceased",
        "discontinued",
        "no longer",
        "resolved",
        "remitted",
    ],
}

THERAPY_NEGATION_TERMS: list[str] = [
    "denies",
    "denied",
    "denying",
    "does not endorse",
    "did not endorse",
    "no complaints of",
    "no reports of",
    "absence of",
    "absent",
    "no longer experiencing",
    "stopped experiencing",
    "resolved",
]

# Cues excluded from the regex tier because they are too common in
# conversational speech and cause false positives (e.g., "Not great"
# triggering a polarity flip when comparing clinical claims to
# conversational transcript segments).
_EXCLUDED_CUES = frozenset({"no ", "not ", "n't "})

# Flat set of all cues for quick lookup (lowercased)
_ALL_CUES: list[str] = sorted(
    {cue for cues in NEGATION_CUES.values() for cue in cues if cue not in _EXCLUDED_CUES},
    key=len,
    reverse=True,  # longest first so we match "no evidence of" before "no "
)

# Stopwords to ignore when comparing content after negation stripping
_STOPWORDS = frozenset(
    {
        "a",
        "an",
        "the",
        "is",
        "are",
        "was",
        "were",
        "be",
        "been",
        "being",
        "have",
        "has",
        "had",
        "do",
        "does",
        "did",
        "will",
        "would",
        "shall",
        "should",
        "may",
        "might",
        "can",
        "could",
        "of",
        "in",
        "to",
        "for",
        "with",
        "on",
        "at",
        "by",
        "from",
        "that",
        "this",
        "it",
        "i",
        "my",
        "me",
        "he",
        "she",
        "they",
        "his",
        "her",
        "their",
        "client",
        "patient",
    }
)


class NegationSignal(VerificationSignal):
    """Detect polarity flips between claim and segment (safety signal).

    Never returns PASS -- only FAIL (polarity flip) or UNCERTAIN.
    """

    @property
    def name(self) -> str:
        return "negation"

    def check(
        self,
        claim_text: str,
        segment_text: str,
        context: SignalContext,  # noqa: ARG002
    ) -> SignalResult:
        # Tier 1: regex fast path
        result = self._regex_check(claim_text, segment_text)
        if result is not None:
            return result

        # Tier 2: negspacy fallback
        result = self._negspacy_check(claim_text, segment_text)
        if result is not None:
            return result

        return SignalResult(
            verdict=SignalVerdict.UNCERTAIN,
            confidence=0.5,
            signal_name=self.name,
            detail="No polarity flip detected",
        )

    # ------------------------------------------------------------------
    # Tier 1: Regex
    # ------------------------------------------------------------------

    def _regex_check(self, claim_text: str, segment_text: str) -> SignalResult | None:
        claim_lower = claim_text.lower()
        segment_lower = segment_text.lower()

        claim_negated, claim_cue = _find_negation_cue(claim_lower)
        segment_negated, segment_cue = _find_negation_cue(segment_lower)

        # XOR: exactly one text is negated
        if claim_negated != segment_negated:
            claim_content = (
                _strip_negation(claim_lower, claim_cue) if claim_negated else claim_lower
            )
            segment_content = (
                _strip_negation(segment_lower, segment_cue) if segment_negated else segment_lower
            )
            if _content_matches(claim_content, segment_content):
                negated_side = "claim" if claim_negated else "segment"
                return SignalResult(
                    verdict=SignalVerdict.FAIL,
                    confidence=0.05,
                    signal_name=self.name,
                    detail=(
                        f"Polarity flip detected: {negated_side} is negated with matching content"
                    ),
                )
            # Negation cue found in one side but content doesn't match -- inconclusive
            return None

        # Both negated or neither: no flip
        return None

    # ------------------------------------------------------------------
    # Tier 2: negspacy (NegEx)
    # ------------------------------------------------------------------

    def _negspacy_check(self, claim_text: str, segment_text: str) -> SignalResult | None:
        """Use negspacy for implicit negation patterns regex might miss."""
        try:
            nlp = _get_negspacy_nlp()
        except (ImportError, OSError):
            # negspacy or spacy model not available -- skip tier 2
            return None

        claim_has_negated_ents = _has_negated_entities(nlp, claim_text)
        segment_has_negated_ents = _has_negated_entities(nlp, segment_text)

        if claim_has_negated_ents != segment_has_negated_ents:
            return SignalResult(
                verdict=SignalVerdict.FAIL,
                confidence=0.05,
                signal_name=self.name,
                detail="Polarity flip detected via NegEx: one text has negated entities",
            )

        return None


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------

_negspacy_nlp: Any = None


def _get_negspacy_nlp() -> Any:
    """Lazy-load spaCy pipeline with negspacy NegEx pipe."""
    global _negspacy_nlp  # noqa: PLW0603
    if _negspacy_nlp is not None:
        return _negspacy_nlp

    import spacy

    try:
        from negspacy.negation import Negex  # noqa: F401 -- importing registers the factory
    except ImportError as err:
        raise ImportError("negspacy is required for Tier 2 negation detection") from err

    nlp = spacy.load("en_core_web_sm")
    nlp.add_pipe(
        "negex",
        config={
            "ent_types": ["PROBLEM", "TREATMENT", "TEST"],
        },
    )
    _negspacy_nlp = nlp
    return nlp


def _has_negated_entities(nlp: Any, text: str) -> bool:
    """Check whether any entity in the text is negated via NegEx."""
    doc = nlp(text)
    return any(getattr(ent._, "negex", False) for ent in doc.ents)


def _find_negation_cue(text: str) -> tuple[bool, str]:
    """Return (is_negated, matched_cue) for the given lowercased text."""
    for cue in _ALL_CUES:
        if cue in text:
            return True, cue
    return False, ""


def _strip_negation(text: str, cue: str) -> str:
    """Remove the negation cue from text and return the remaining content."""
    if not cue:
        return text
    idx = text.index(cue)
    return text[idx + len(cue) :].strip()


def _content_matches(content_a: str, content_b: str) -> bool:
    """Check whether two content strings share enough meaningful tokens."""
    tokens_a = _content_tokens(content_a)
    tokens_b = _content_tokens(content_b)

    if not tokens_a or not tokens_b:
        return False

    overlap = tokens_a & tokens_b
    # Require at least one overlapping content word and >= 30% overlap
    # relative to the smaller set
    min_overlap_ratio = 0.3
    smaller = min(len(tokens_a), len(tokens_b))
    return len(overlap) >= 1 and len(overlap) / smaller >= min_overlap_ratio


def _content_tokens(text: str) -> set[str]:
    """Extract meaningful content tokens from text."""
    words = set(re.findall(r"[a-z]+", text.lower()))
    return words - _STOPWORDS
