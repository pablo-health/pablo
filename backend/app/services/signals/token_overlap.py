# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Stemmed token overlap verification signal with clinical synonym expansion.

Catches the ~35-40% of claim-segment pairs where the claim is a short
clinical phrase with key terms that appear (possibly inflected) in the
transcript segment. Uses spaCy lemmatization and a static clinical synonym
map to match terms like "Sweating" <-> "I was sweating".
"""

from __future__ import annotations

from typing import Any

from ..verification_signals import (
    SignalContext,
    SignalResult,
    SignalVerdict,
    VerificationSignal,
)

_PASS_THRESHOLD = 0.70
_FAIL_THRESHOLD = 0.10
_FAIL_MIN_TOKENS = 3
_MAX_PASS_CONFIDENCE = 0.85

# Clinical terms mapped to their conversational equivalents.
# Bidirectional: if "insomnia" is a claim term, we also check for "sleep" etc.
CLINICAL_SYNONYMS: dict[str, set[str]] = {
    "insomnia": {"sleep", "sleeping", "asleep", "awake", "waking"},
    "tachycardia": {"heart", "racing", "pounding", "fast"},
    "dyspnea": {"breath", "breathe", "breathing", "breathless"},
    "anxiety": {"anxious", "worried", "nervous", "worry"},
    "depression": {"depressed", "sad", "hopeless", "down"},
    "ideation": {"thought", "thinking", "idea"},
    "suicidal": {"suicide", "kill", "die", "death", "end"},
    "homicidal": {"homicide", "harm", "hurt", "violence"},
    "somnolence": {"sleepy", "drowsy", "tired", "fatigue"},
    "cbt": {"cognitive", "restructuring", "thought", "reframe"},
    "pmr": {"progressive", "muscle", "relaxation", "tense"},
    "affect": {"mood", "emotion", "feeling"},
    "hyperventilation": {"breathing", "fast", "rapid", "shortness"},
    "panic": {"panic", "attack", "fear", "terror"},
    "rumination": {"ruminate", "overthinking", "dwelling", "obsess"},
    "anhedonia": {"pleasure", "interest", "enjoy", "motivation"},
}


def _build_synonym_lookup() -> dict[str, set[str]]:
    """Build a bidirectional synonym lookup from CLINICAL_SYNONYMS.

    For each entry (key -> values), creates mappings:
    - key -> values
    - each value -> {key} | other_values
    """
    lookup: dict[str, set[str]] = {}
    for key, values in CLINICAL_SYNONYMS.items():
        lookup.setdefault(key, set()).update(values)
        for val in values:
            synonyms = {key} | values - {val}
            lookup.setdefault(val, set()).update(synonyms)
    return lookup


_SYNONYM_LOOKUP = _build_synonym_lookup()

# POS tags to keep as content tokens (nouns, verbs, adjectives, adverbs)
_CONTENT_POS = {"NOUN", "VERB", "ADJ", "ADV", "PROPN"}


class TokenOverlapSignal(VerificationSignal):
    """Stemmed token overlap with clinical synonym expansion."""

    def __init__(self, spacy_model: str = "en_core_web_lg") -> None:
        self._spacy_model = spacy_model
        self._nlp: Any = None

    def _get_nlp(self) -> Any:
        if self._nlp is None:
            import spacy

            self._nlp = spacy.load(self._spacy_model)
        return self._nlp

    @property
    def name(self) -> str:
        return "token_overlap"

    def check(
        self,
        claim_text: str,
        segment_text: str,
        _context: SignalContext,
    ) -> SignalResult:
        claim_lemmas = self._extract_content_lemmas(claim_text)
        segment_lemmas = self._extract_content_lemmas(segment_text)

        if not claim_lemmas:
            return SignalResult(
                verdict=SignalVerdict.UNCERTAIN,
                confidence=0.0,
                signal_name=self.name,
                detail="No content tokens in claim",
            )

        # For each claim lemma, check if IT or ANY of its synonyms appear
        # in the segment. This counts per-concept coverage rather than
        # per-expanded-term, avoiding denominator inflation from synonyms.
        matched_lemmas: set[str] = set()
        for lemma in claim_lemmas:
            candidates = {lemma} | _SYNONYM_LOOKUP.get(lemma, set())
            if candidates & segment_lemmas:
                matched_lemmas.add(lemma)

        ratio = len(matched_lemmas) / len(claim_lemmas)

        if ratio >= _PASS_THRESHOLD:
            confidence = min(_MAX_PASS_CONFIDENCE, 0.6 + ratio * 0.3)
            return SignalResult(
                verdict=SignalVerdict.PASS,
                confidence=confidence,
                signal_name=self.name,
                detail=f"Token overlap {ratio:.0%}: {sorted(matched_lemmas)}",
            )

        if ratio <= _FAIL_THRESHOLD and len(claim_lemmas) >= _FAIL_MIN_TOKENS:
            return SignalResult(
                verdict=SignalVerdict.FAIL,
                confidence=0.1,
                signal_name=self.name,
                detail=f"Token overlap {ratio:.0%}: near-zero match",
            )

        return SignalResult(
            verdict=SignalVerdict.UNCERTAIN,
            confidence=ratio * 0.5,
            signal_name=self.name,
            detail=f"Token overlap {ratio:.0%}: ambiguous",
        )

    def _extract_content_lemmas(self, text: str) -> set[str]:
        """Extract content word lemmas and lowercase forms from text using spaCy.

        Includes both the lemma and the lowercase surface form for each content
        token, ensuring matches across different inflections (e.g. "racing" as
        adjective lemmatizes to "racing" but as verb lemmatizes to "race").
        """
        nlp = self._get_nlp()
        doc = nlp(text)
        result: set[str] = set()
        for token in doc:
            if (
                not token.is_stop
                and not token.is_punct
                and token.pos_ in _CONTENT_POS
                and len(token.text) > 1
            ):
                result.add(token.lemma_.lower())
                result.add(token.text.lower())
        return result
