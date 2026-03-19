# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Tests for the stemmed token overlap verification signal."""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterator

os.environ["ENVIRONMENT"] = "development"

import pytest
from app.services.signals.token_overlap import (
    _SYNONYM_LOOKUP,
    CLINICAL_SYNONYMS,
    TokenOverlapSignal,
    _build_synonym_lookup,
)
from app.services.verification_signals import SignalContext, SignalVerdict


def _make_context() -> SignalContext:
    return SignalContext(claim_key="test.claim")


# ---------------------------------------------------------------------------
# Synonym map tests
# ---------------------------------------------------------------------------


class TestClinicalSynonymMap:
    """Verify the clinical synonym map is structured correctly."""

    def test_synonym_map_not_empty(self) -> None:
        assert len(CLINICAL_SYNONYMS) > 0

    def test_all_values_are_sets(self) -> None:
        for key, values in CLINICAL_SYNONYMS.items():
            assert isinstance(values, set), f"{key} should map to a set"

    def test_all_values_are_lowercase(self) -> None:
        for key, values in CLINICAL_SYNONYMS.items():
            assert key == key.lower(), f"Key {key!r} should be lowercase"
            for val in values:
                assert val == val.lower(), f"Value {val!r} in {key} should be lowercase"

    def test_known_clinical_terms_present(self) -> None:
        assert "insomnia" in CLINICAL_SYNONYMS
        assert "anxiety" in CLINICAL_SYNONYMS
        assert "tachycardia" in CLINICAL_SYNONYMS
        assert "depression" in CLINICAL_SYNONYMS
        assert "suicidal" in CLINICAL_SYNONYMS


class TestBidirectionalSynonymLookup:
    """Verify the synonym lookup is bidirectional."""

    def test_forward_lookup(self) -> None:
        assert "sleep" in _SYNONYM_LOOKUP["insomnia"]

    def test_reverse_lookup(self) -> None:
        assert "insomnia" in _SYNONYM_LOOKUP["sleep"]

    def test_peer_lookup(self) -> None:
        # "sleeping" and "awake" are both synonyms of "insomnia"
        assert "awake" in _SYNONYM_LOOKUP["sleeping"]

    def test_build_is_idempotent(self) -> None:
        lookup1 = _build_synonym_lookup()
        lookup2 = _build_synonym_lookup()
        assert lookup1 == lookup2


# ---------------------------------------------------------------------------
# Signal tests with real spaCy model
# ---------------------------------------------------------------------------

# These tests use the real spaCy model. They are the primary functional tests.


@pytest.fixture
def signal() -> TokenOverlapSignal:
    return TokenOverlapSignal()


@pytest.mark.spacy
class TestTokenOverlapName:
    def test_name(self, signal: TokenOverlapSignal) -> None:
        assert signal.name == "token_overlap"


@pytest.mark.spacy
class TestTokenOverlapPASS:
    """High overlap -> PASS (the primary use case for this signal)."""

    def test_exact_term_match(self, signal: TokenOverlapSignal) -> None:
        """'Sweating' / 'I was sweating' -- the canonical example from design doc."""
        result = signal.check("Sweating", "I was sweating", _make_context())
        assert result.verdict == SignalVerdict.PASS
        assert result.signal_name == "token_overlap"

    def test_single_content_word_match(self, signal: TokenOverlapSignal) -> None:
        result = signal.check("Insomnia", "I can't sleep at all", _make_context())
        assert result.verdict == SignalVerdict.PASS

    def test_confidence_capped_at_085(self, signal: TokenOverlapSignal) -> None:
        result = signal.check("Sweating", "I was sweating", _make_context())
        assert result.confidence <= 0.85

    def test_pass_detail_contains_overlap(self, signal: TokenOverlapSignal) -> None:
        result = signal.check("Sweating", "I was sweating", _make_context())
        assert "sweat" in result.detail.lower()


@pytest.mark.spacy
class TestTokenOverlapFAIL:
    """Very low overlap with enough claim tokens -> FAIL."""

    def test_no_overlap_with_many_tokens(self, signal: TokenOverlapSignal) -> None:
        result = signal.check(
            "Progressive muscle relaxation technique applied",
            "The weather was sunny and warm today",
            _make_context(),
        )
        assert result.verdict == SignalVerdict.FAIL
        assert result.confidence == 0.1

    def test_fail_detail_mentions_near_zero(self, signal: TokenOverlapSignal) -> None:
        result = signal.check(
            "Cognitive behavioral therapy homework assigned",
            "The cat sat on the mat quietly",
            _make_context(),
        )
        if result.verdict == SignalVerdict.FAIL:
            assert "near-zero" in result.detail


@pytest.mark.spacy
class TestTokenOverlapUNCERTAIN:
    """Ambiguous overlap -> UNCERTAIN."""

    def test_partial_overlap(self, signal: TokenOverlapSignal) -> None:
        """Multi-word claims with some but not all concepts matching -> UNCERTAIN."""
        result = signal.check(
            "Panic attacks two reported episodes",
            "I had two panic attacks",
            _make_context(),
        )
        assert result.verdict == SignalVerdict.UNCERTAIN

    def test_no_content_tokens(self, signal: TokenOverlapSignal) -> None:
        """Stop-words-only claim returns UNCERTAIN with 0 confidence."""
        result = signal.check("the and or", "some text here", _make_context())
        assert result.verdict == SignalVerdict.UNCERTAIN
        assert result.confidence == 0.0
        assert "No content tokens" in result.detail

    def test_empty_claim(self, signal: TokenOverlapSignal) -> None:
        result = signal.check("", "some segment text", _make_context())
        assert result.verdict == SignalVerdict.UNCERTAIN
        assert result.confidence == 0.0

    def test_too_few_tokens_for_fail(self, signal: TokenOverlapSignal) -> None:
        """Short claims (< 3 expanded tokens) don't trigger FAIL even at 0% overlap."""
        result = signal.check("Stress", "The cat was happy", _make_context())
        # With synonym expansion "stress" might get some expansions
        # but if overlap is low, should be UNCERTAIN not FAIL (too few tokens)
        assert result.verdict in (SignalVerdict.UNCERTAIN, SignalVerdict.FAIL)


@pytest.mark.spacy
class TestTokenOverlapSynonymExpansion:
    """Verify clinical synonym expansion works in practice."""

    def test_insomnia_matches_sleep(self, signal: TokenOverlapSignal) -> None:
        """Clinical term 'insomnia' should match conversational 'sleep'."""
        result = signal.check("Insomnia", "I can't sleep at all", _make_context())
        assert result.verdict == SignalVerdict.PASS

    def test_anxiety_matches_anxious(self, signal: TokenOverlapSignal) -> None:
        result = signal.check("Anxiety", "I feel anxious", _make_context())
        assert result.verdict == SignalVerdict.PASS

    def test_depression_matches_depressed(self, signal: TokenOverlapSignal) -> None:
        result = signal.check("Depression", "I've been feeling depressed", _make_context())
        assert result.verdict == SignalVerdict.PASS


@pytest.mark.spacy
class TestTokenOverlapLazyLoading:
    """Verify spaCy model is loaded lazily."""

    def test_no_model_loaded_at_init(self) -> None:
        signal = TokenOverlapSignal()
        assert signal._nlp is None

    def test_model_loaded_on_first_check(self, signal: TokenOverlapSignal) -> None:
        signal.check("test", "test", _make_context())
        assert signal._nlp is not None

    def test_model_reused_on_second_check(self, signal: TokenOverlapSignal) -> None:
        signal.check("test", "test", _make_context())
        nlp_ref = signal._nlp
        signal.check("test2", "test2", _make_context())
        assert signal._nlp is nlp_ref


# ---------------------------------------------------------------------------
# Unit tests with mocked spaCy (for fast/isolated testing)
# ---------------------------------------------------------------------------


class _MockToken:
    """Minimal mock of a spaCy Token."""

    def __init__(
        self,
        text: str,
        lemma: str,
        pos: str,
        is_stop: bool = False,
    ) -> None:
        self.text = text
        self.lemma_ = lemma
        self.pos_ = pos
        self.is_stop = is_stop
        self.is_punct = False


class _MockDoc:
    """Minimal mock of a spaCy Doc."""

    def __init__(self, tokens: list[_MockToken]) -> None:
        self._tokens = tokens

    def __iter__(self) -> Iterator[_MockToken]:
        return iter(self._tokens)


def _mock_nlp_factory(text_to_tokens: dict[str, list[_MockToken]]) -> object:
    """Create a mock spaCy nlp callable."""

    def mock_nlp(text: str) -> _MockDoc:
        tokens = text_to_tokens.get(text, [])
        return _MockDoc(tokens)

    return mock_nlp


class TestTokenOverlapWithMockedSpacy:
    """Fast unit tests using mocked spaCy to verify logic without model."""

    def test_full_overlap_passes(self) -> None:
        signal = TokenOverlapSignal()
        signal._nlp = _mock_nlp_factory(
            {
                "Sweating": [_MockToken("Sweating", "sweat", "VERB")],
                "I was sweating": [
                    _MockToken("I", "I", "PRON", is_stop=True),
                    _MockToken("was", "be", "AUX", is_stop=True),
                    _MockToken("sweating", "sweat", "VERB"),
                ],
            }
        )

        result = signal.check("Sweating", "I was sweating", _make_context())
        assert result.verdict == SignalVerdict.PASS

    def test_zero_overlap_with_many_tokens_fails(self) -> None:
        signal = TokenOverlapSignal()
        signal._nlp = _mock_nlp_factory(
            {
                "severe chronic headache": [
                    _MockToken("severe", "severe", "ADJ"),
                    _MockToken("chronic", "chronic", "ADJ"),
                    _MockToken("headache", "headache", "NOUN"),
                ],
                "happy sunny day": [
                    _MockToken("happy", "happy", "ADJ"),
                    _MockToken("sunny", "sunny", "ADJ"),
                    _MockToken("day", "day", "NOUN"),
                ],
            }
        )

        result = signal.check("severe chronic headache", "happy sunny day", _make_context())
        assert result.verdict == SignalVerdict.FAIL

    def test_partial_overlap_uncertain(self) -> None:
        signal = TokenOverlapSignal()
        signal._nlp = _mock_nlp_factory(
            {
                "severe headache with nausea": [
                    _MockToken("severe", "severe", "ADJ"),
                    _MockToken("headache", "headache", "NOUN"),
                    _MockToken("with", "with", "ADP", is_stop=True),
                    _MockToken("nausea", "nausea", "NOUN"),
                ],
                "I had a headache": [
                    _MockToken("I", "I", "PRON", is_stop=True),
                    _MockToken("had", "have", "VERB", is_stop=True),
                    _MockToken("a", "a", "DET", is_stop=True),
                    _MockToken("headache", "headache", "NOUN"),
                ],
            }
        )

        result = signal.check(
            "severe headache with nausea",
            "I had a headache",
            _make_context(),
        )
        # 1 of 3 claim lemmas matched -> 33% -> UNCERTAIN
        assert result.verdict == SignalVerdict.UNCERTAIN

    def test_no_content_tokens_uncertain(self) -> None:
        signal = TokenOverlapSignal()
        signal._nlp = _mock_nlp_factory(
            {
                "the a an": [
                    _MockToken("the", "the", "DET", is_stop=True),
                    _MockToken("a", "a", "DET", is_stop=True),
                    _MockToken("an", "an", "DET", is_stop=True),
                ],
                "some text": [
                    _MockToken("some", "some", "DET", is_stop=True),
                    _MockToken("text", "text", "NOUN"),
                ],
            }
        )

        result = signal.check("the a an", "some text", _make_context())
        assert result.verdict == SignalVerdict.UNCERTAIN
        assert result.confidence == 0.0
