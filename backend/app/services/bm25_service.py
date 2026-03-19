# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""BM25 lexical relevance scoring for claim-to-segment matching."""

from __future__ import annotations

import math

_ZERO_SCORE = 0.0


class BM25Scorer:
    """BM25 lexical relevance scoring for claim-to-segment matching.

    Pure Python implementation of Okapi BM25 with configurable k1 and b parameters.
    Tokenizes by whitespace + lowercasing.
    """

    def __init__(self, documents: list[str], k1: float = 1.5, b: float = 0.75) -> None:
        """Initialize with corpus of transcript segments."""
        self.k1 = k1
        self.b = b
        self.doc_tokens = [self._tokenize(doc) for doc in documents]
        self.doc_count = len(documents)
        self.doc_lengths = [len(tokens) for tokens in self.doc_tokens]
        self.avgdl = sum(self.doc_lengths) / self.doc_count if self.doc_count > 0 else 0.0

        # Build document frequency: how many docs contain each term
        self.df: dict[str, int] = {}
        for tokens in self.doc_tokens:
            for term in set(tokens):
                self.df[term] = self.df.get(term, 0) + 1

    @staticmethod
    def _tokenize(text: str) -> list[str]:
        """Tokenize by whitespace and lowercase."""
        return text.lower().split()

    def _idf(self, term: str) -> float:
        """Compute inverse document frequency for a term."""
        df = self.df.get(term, 0)
        # Standard BM25 IDF with smoothing to avoid negative values
        return math.log((self.doc_count - df + 0.5) / (df + 0.5) + 1.0)

    def score(self, query: str) -> list[float]:
        """Score all documents against a query. Returns list of BM25 scores."""
        if self.doc_count == 0:
            return []

        query_terms = self._tokenize(query)
        if not query_terms:
            return [0.0] * self.doc_count

        scores: list[float] = []
        for i, doc_tokens in enumerate(self.doc_tokens):
            doc_len = self.doc_lengths[i]
            # Build term frequency map for this document
            tf_map: dict[str, int] = {}
            for token in doc_tokens:
                tf_map[token] = tf_map.get(token, 0) + 1

            doc_score = 0.0
            for term in query_terms:
                tf = tf_map.get(term, 0)
                if tf == 0:
                    continue
                idf = self._idf(term)
                numerator = tf * (self.k1 + 1)
                denominator = tf + self.k1 * (1 - self.b + self.b * doc_len / self.avgdl)
                doc_score += idf * numerator / denominator

            scores.append(doc_score)

        return scores

    def top_k(self, query: str, k: int = 5) -> list[tuple[int, float]]:
        """Return top-k (doc_index, score) pairs for a query, sorted descending."""
        scores = self.score(query)
        indexed = [(i, s) for i, s in enumerate(scores) if s > _ZERO_SCORE]
        indexed.sort(key=lambda x: x[1], reverse=True)
        return indexed[:k]
