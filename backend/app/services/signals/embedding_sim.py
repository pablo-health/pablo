# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Embedding similarity verification signal.

Uses pre-computed cosine similarity from Stage 1 candidate retrieval
(passed via SignalContext.embedding_similarity) to verify claim-segment pairs.
"""

from __future__ import annotations

from ..verification_signals import (
    SignalContext,
    SignalResult,
    SignalVerdict,
    VerificationSignal,
)

_PASS_THRESHOLD = 0.85
_FAIL_THRESHOLD = 0.30
_MAX_PASS_CONFIDENCE = 0.90


class EmbeddingSimilaritySignal(VerificationSignal):
    """Cosine similarity from Stage 1 embeddings."""

    @property
    def name(self) -> str:
        return "embedding_sim"

    def check(
        self,
        _claim_text: str,
        _segment_text: str,
        context: SignalContext,
    ) -> SignalResult:
        sim = context.embedding_similarity

        if sim >= _PASS_THRESHOLD:
            return SignalResult(
                verdict=SignalVerdict.PASS,
                confidence=min(_MAX_PASS_CONFIDENCE, sim),
                signal_name=self.name,
                detail=f"Embedding similarity {sim:.3f}",
            )

        if sim <= _FAIL_THRESHOLD:
            return SignalResult(
                verdict=SignalVerdict.FAIL,
                confidence=sim * 0.3,
                signal_name=self.name,
                detail=f"Embedding similarity {sim:.3f}: very low",
            )

        return SignalResult(
            verdict=SignalVerdict.UNCERTAIN,
            confidence=sim * 0.7,
            signal_name=self.name,
            detail=f"Embedding similarity {sim:.3f}: ambiguous range",
        )
