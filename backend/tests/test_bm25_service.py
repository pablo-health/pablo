# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Tests for BM25 lexical scoring utility."""

import os

os.environ["ENVIRONMENT"] = "development"

from app.services.bm25_service import BM25Scorer


class TestBM25BasicScoring:
    """Test basic BM25 scoring behavior."""

    def test_matching_doc_scores_higher(self) -> None:
        docs = [
            "the cat sat on the mat",
            "the dog played in the park",
            "a cat and a dog are friends",
        ]
        scorer = BM25Scorer(docs)
        scores = scorer.score("cat")
        # Docs 0 and 2 contain "cat", doc 1 does not
        assert scores[0] > 0.0
        assert scores[1] == 0.0
        assert scores[2] > 0.0

    def test_doc_with_all_query_terms_scores_highest(self) -> None:
        docs = [
            "patient reports anxiety and insomnia",
            "patient reports anxiety",
            "insomnia is a common symptom",
        ]
        scorer = BM25Scorer(docs)
        scores = scorer.score("anxiety insomnia")
        # Doc 0 contains both terms, should score highest
        assert scores[0] > scores[1]
        assert scores[0] > scores[2]

    def test_scores_are_non_negative(self) -> None:
        docs = ["hello world", "foo bar", "baz qux"]
        scorer = BM25Scorer(docs)
        scores = scorer.score("hello")
        assert all(s >= 0.0 for s in scores)

    def test_no_match_returns_zero_scores(self) -> None:
        docs = ["hello world", "foo bar"]
        scorer = BM25Scorer(docs)
        scores = scorer.score("nonexistent")
        assert scores == [0.0, 0.0]


class TestBM25TopK:
    """Test top-k retrieval."""

    def test_top_k_returns_correct_ordering(self) -> None:
        docs = [
            "therapy session notes for patient",
            "patient discussed anxiety and depression",
            "weather was nice today",
            "patient anxiety levels were high during therapy",
        ]
        scorer = BM25Scorer(docs)
        results = scorer.top_k("patient anxiety", k=2)
        assert len(results) == 2
        # Results should be sorted by score descending
        assert results[0][1] >= results[1][1]

    def test_top_k_limits_results(self) -> None:
        docs = [f"document {i} word" for i in range(10)]
        scorer = BM25Scorer(docs)
        results = scorer.top_k("word", k=3)
        assert len(results) == 3

    def test_top_k_returns_fewer_than_k_if_not_enough_matches(self) -> None:
        docs = ["cat sat", "dog played", "bird flew"]
        scorer = BM25Scorer(docs)
        results = scorer.top_k("cat", k=5)
        assert len(results) == 1
        assert results[0][0] == 0

    def test_top_k_excludes_zero_score_docs(self) -> None:
        docs = ["cat sat", "dog played"]
        scorer = BM25Scorer(docs)
        results = scorer.top_k("cat", k=5)
        # Only doc 0 matches
        assert len(results) == 1
        assert results[0][0] == 0

    def test_top_k_returns_doc_index_and_score(self) -> None:
        docs = ["hello world", "hello there"]
        scorer = BM25Scorer(docs)
        results = scorer.top_k("hello", k=2)
        for idx, score in results:
            assert isinstance(idx, int)
            assert isinstance(score, float)
            assert score > 0.0


class TestBM25EdgeCases:
    """Test edge cases and empty inputs."""

    def test_empty_documents(self) -> None:
        scorer = BM25Scorer([])
        assert scorer.score("query") == []
        assert scorer.top_k("query") == []

    def test_empty_query(self) -> None:
        docs = ["hello world"]
        scorer = BM25Scorer(docs)
        scores = scorer.score("")
        assert scores == [0.0]

    def test_whitespace_only_query(self) -> None:
        docs = ["hello world"]
        scorer = BM25Scorer(docs)
        scores = scorer.score("   ")
        assert scores == [0.0]

    def test_single_word_documents(self) -> None:
        docs = ["hello", "world", "hello"]
        scorer = BM25Scorer(docs)
        scores = scorer.score("hello")
        assert scores[0] > 0.0
        assert scores[1] == 0.0
        assert scores[2] > 0.0

    def test_empty_string_documents(self) -> None:
        docs = ["", "hello", ""]
        scorer = BM25Scorer(docs)
        scores = scorer.score("hello")
        assert scores[0] == 0.0
        assert scores[1] > 0.0
        assert scores[2] == 0.0

    def test_case_insensitive_matching(self) -> None:
        docs = ["Patient Reports Anxiety"]
        scorer = BM25Scorer(docs)
        scores = scorer.score("patient reports anxiety")
        assert scores[0] > 0.0

    def test_repeated_terms_in_query(self) -> None:
        docs = ["the cat sat on the mat"]
        scorer = BM25Scorer(docs)
        score_single = scorer.score("cat")
        score_repeated = scorer.score("cat cat cat")
        # Repeated query terms should boost score
        assert score_repeated[0] > score_single[0]


class TestBM25Parameters:
    """Test configurable k1 and b parameters."""

    def test_custom_k1_and_b(self) -> None:
        docs = ["hello world", "hello there friend"]
        scorer = BM25Scorer(docs, k1=1.2, b=0.5)
        scores = scorer.score("hello")
        assert len(scores) == 2
        assert all(s > 0.0 for s in scores)

    def test_different_k1_gives_different_scores(self) -> None:
        # Use docs with different lengths so k1 changes term frequency saturation
        docs = [
            "patient patient patient anxiety",
            "patient discussed feelings about therapy sessions today",
        ]
        scorer_low = BM25Scorer(docs, k1=0.5)
        scorer_high = BM25Scorer(docs, k1=2.0)
        scores_low = scorer_low.score("patient")
        scores_high = scorer_high.score("patient")
        # Different k1 values change how much repeated terms boost the score
        assert scores_low != scores_high
