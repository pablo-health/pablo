# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Hybrid multi-signal verification pipeline for source attributions.

Stage 1: Candidate retrieval (BM25 + embedding similarity) with preserved scores.
Stage 2: Hybrid signal chain verification (replaces NLI-only).
Stage 3: LLM spot-check (TODO -- future implementation).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from ..models.session import CONFIDENCE_THRESHOLDS
from .bm25_service import BM25Scorer
from .embedding_service import EmbeddingService, cosine_similarity
from .verification_signals import (
    SignalContext,
    SignalResult,
    SignalVerdict,
    VerificationSignal,
)

if TYPE_CHECKING:
    from .nli_service import NLIService

logger = logging.getLogger(__name__)

_TOP_K_CANDIDATES = 5
_POSSIBLE_MATCH_THRESHOLD = 0.5


@dataclass
class CandidateResult:
    """Stage 1 candidate retrieval results with preserved scores."""

    candidate_ids: dict[str, list[int]]
    embedding_scores: dict[str, dict[int, float]]
    claim_embeddings: list[list[float]]
    segment_embeddings: list[list[float]]


@dataclass
class VerificationResult:
    """Result of verifying a single claim's source attributions."""

    claim_key: str
    original_segment_ids: list[int]
    confidence_score: float = 0.0
    confidence_level: str = "unverified"
    possible_match_segment_ids: list[int] = field(default_factory=list)
    signal_used: str = ""


def _score_to_level(score: float) -> str:
    """Map a confidence score to a confidence level using thresholds."""
    if score >= CONFIDENCE_THRESHOLDS["verified"]:
        return "verified"
    if score >= CONFIDENCE_THRESHOLDS["high"]:
        return "high"
    if score >= CONFIDENCE_THRESHOLDS["medium"]:
        return "medium"
    if score >= CONFIDENCE_THRESHOLDS["low"]:
        return "low"
    return "unverified"


class SourceVerificationService:
    """Hybrid multi-signal verification pipeline for source attributions.

    Stage 1: Candidate retrieval (BM25 + embedding similarity)
    Stage 2: Hybrid signal chain verification
    Stage 3: LLM spot-check (TODO -- future implementation)
    """

    def __init__(
        self,
        embedding_service: EmbeddingService,
        nli_service: NLIService,
        primary_signals: list[VerificationSignal] | None = None,
        safety_signals: list[VerificationSignal] | None = None,
    ) -> None:
        self.embedding_service = embedding_service
        self.nli_service = nli_service
        self.primary_signals = primary_signals or []
        self.safety_signals = safety_signals or []

    def verify_attributions(
        self,
        claims: dict[str, str],
        segment_texts: list[str],
        attributions: dict[str, list[int]],
    ) -> list[VerificationResult]:
        """Run the verification pipeline.

        Uses hybrid signal chain if signals are configured, otherwise
        falls back to NLI-only verification.
        """
        if not claims or not segment_texts:
            return [
                VerificationResult(
                    claim_key=key,
                    original_segment_ids=attributions.get(key, []),
                )
                for key in claims
            ]

        # Stage 1: Candidate retrieval with preserved scores
        candidate_result = self._retrieve_candidates_with_scores(claims, segment_texts)

        # Stage 2: Hybrid or NLI verification
        if self.primary_signals or self.safety_signals:
            return self._hybrid_verify(
                claims,
                segment_texts,
                attributions,
                candidate_result,
            )
        return self._verify_with_nli(
            claims,
            segment_texts,
            attributions,
            candidate_result.candidate_ids,
        )

    def _retrieve_candidates_with_scores(
        self,
        claims: dict[str, str],
        segment_texts: list[str],
    ) -> CandidateResult:
        """Stage 1: Retrieve candidates via BM25 + embedding with preserved scores.

        Returns CandidateResult containing both candidate indices and embedding
        similarity scores for use by downstream signals.
        """
        # BM25 candidates
        bm25 = BM25Scorer(segment_texts)
        bm25_candidates: dict[str, list[int]] = {}
        for key, text in claims.items():
            top = bm25.top_k(text, k=_TOP_K_CANDIDATES)
            bm25_candidates[key] = [idx for idx, _score in top]

        # Embedding candidates
        claim_keys = list(claims.keys())
        claim_texts = [claims[k] for k in claim_keys]

        claim_embeddings = self.embedding_service.embed_texts(claim_texts)
        segment_embeddings = self.embedding_service.embed_texts(segment_texts)

        embedding_candidates: dict[str, list[int]] = {}
        embedding_scores: dict[str, dict[int, float]] = {}
        for i, key in enumerate(claim_keys):
            sims = [
                (j, cosine_similarity(claim_embeddings[i], seg_emb))
                for j, seg_emb in enumerate(segment_embeddings)
            ]
            # Store all similarity scores for this claim
            embedding_scores[key] = dict(sims)
            sims.sort(key=lambda x: x[1], reverse=True)
            embedding_candidates[key] = [idx for idx, _sim in sims[:_TOP_K_CANDIDATES]]

        # Merge and deduplicate, keeping top-k by combined rank
        merged: dict[str, list[int]] = {}
        for key in claims:
            seen: set[int] = set()
            combined: list[int] = []
            bm25_list = bm25_candidates.get(key, [])
            emb_list = embedding_candidates.get(key, [])
            for idx in _interleave(bm25_list, emb_list):
                if idx not in seen:
                    seen.add(idx)
                    combined.append(idx)
                    if len(combined) >= _TOP_K_CANDIDATES:
                        break
            merged[key] = combined

        return CandidateResult(
            candidate_ids=merged,
            embedding_scores=embedding_scores,
            claim_embeddings=claim_embeddings,
            segment_embeddings=segment_embeddings,
        )

    def _hybrid_verify(
        self,
        claims: dict[str, str],
        segment_texts: list[str],
        attributions: dict[str, list[int]],
        candidate_result: CandidateResult,
    ) -> list[VerificationResult]:
        """Stage 2: Hybrid multi-signal verification.

        For each claim:
        1. Concatenate all attributed segments into one document and run
           primary signals against the combined text (SOAP claims typically
           summarize multiple transcript segments).
        2. Run safety signals per-segment (negation, entity, temporal checks
           need individual segment context to catch polarity flips).
        3. Safety FAIL overrides a primary PASS.
        """
        results: list[VerificationResult] = []

        for key, claim_text in claims.items():
            attributed_ids = attributions.get(key, [])
            candidate_ids = candidate_result.candidate_ids.get(key, [])
            scores_for_claim = candidate_result.embedding_scores.get(key, {})

            if not attributed_ids:
                results.append(
                    VerificationResult(
                        claim_key=key,
                        original_segment_ids=attributed_ids,
                    )
                )
                continue

            valid_ids = [sid for sid in attributed_ids if sid < len(segment_texts)]
            if not valid_ids:
                results.append(
                    VerificationResult(
                        claim_key=key,
                        original_segment_ids=attributed_ids,
                    )
                )
                continue

            # Phase 1: Primary signals on concatenated attributed segments
            combined_text = " ".join(segment_texts[sid] for sid in valid_ids)
            max_emb_sim = max(
                (scores_for_claim.get(sid, 0.0) for sid in valid_ids),
                default=0.0,
            )
            combined_context = SignalContext(
                claim_key=key,
                attributed_segment_ids=attributed_ids,
                candidate_segment_ids=candidate_ids,
                embedding_similarity=max_emb_sim,
                all_segment_texts=segment_texts,
            )

            primary_result: SignalResult | None = None
            for signal in self.primary_signals:
                result = signal.check(claim_text, combined_text, combined_context)
                if result.verdict in (SignalVerdict.PASS, SignalVerdict.FAIL):
                    primary_result = result
                    break

            if primary_result is None:
                primary_result = SignalResult(
                    verdict=SignalVerdict.UNCERTAIN,
                    confidence=0.0,
                    signal_name="none",
                    detail="All primary signals returned uncertain",
                )

            # Phase 2: Safety signals per-segment (can override PASS → FAIL)
            final_result = self._run_safety_checks(
                claim_text,
                segment_texts,
                valid_ids,
                attributed_ids,
                candidate_ids,
                scores_for_claim,
                primary_result,
            )

            # Find possible matches from candidates not in attribution
            attributed_set = set(attributed_ids)
            possible_matches: list[int] = []
            for cid in candidate_ids:
                if cid not in attributed_set and cid < len(segment_texts):
                    emb_sim = scores_for_claim.get(cid, 0.0)
                    if emb_sim > _POSSIBLE_MATCH_THRESHOLD:
                        possible_matches.append(cid)

            confidence_level = _score_to_level(final_result.confidence)

            results.append(
                VerificationResult(
                    claim_key=key,
                    original_segment_ids=attributed_ids,
                    confidence_score=final_result.confidence,
                    confidence_level=confidence_level,
                    possible_match_segment_ids=possible_matches,
                    signal_used=final_result.signal_name,
                )
            )

        return results

    def _run_safety_checks(
        self,
        claim_text: str,
        segment_texts: list[str],
        valid_ids: list[int],
        attributed_ids: list[int],
        candidate_ids: list[int],
        scores_for_claim: dict[int, float],
        primary_result: SignalResult,
    ) -> SignalResult:
        """Run safety signals per-segment, overriding PASS → FAIL if needed."""
        for sid in valid_ids:
            seg_context = SignalContext(
                claim_key=primary_result.signal_name,
                attributed_segment_ids=attributed_ids,
                candidate_segment_ids=candidate_ids,
                embedding_similarity=scores_for_claim.get(sid, 0.0),
                all_segment_texts=segment_texts,
            )
            for safety_signal in self.safety_signals:
                safety_result = safety_signal.check(
                    claim_text,
                    segment_texts[sid],
                    seg_context,
                )
                if safety_result.verdict == SignalVerdict.FAIL:
                    return SignalResult(
                        verdict=SignalVerdict.FAIL,
                        confidence=safety_result.confidence,
                        signal_name=safety_result.signal_name,
                        detail=(
                            f"Safety override ({safety_result.signal_name}): "
                            f"{safety_result.detail}; "
                            f"overrode {primary_result.signal_name}"
                        ),
                    )
        return primary_result

    def _run_signal_chain(
        self,
        claim_text: str,
        segment_text: str,
        context: SignalContext,
    ) -> SignalResult:
        """Run a claim-segment pair through the signal chain.

        Phase 1: Primary signals (short-circuit on PASS or FAIL).
        Phase 2: Safety signals (FAIL overrides Phase 1 PASS).
        """
        # Phase 1: Primary signals
        primary_result: SignalResult | None = None
        for signal in self.primary_signals:
            result = signal.check(claim_text, segment_text, context)
            if result.verdict in (SignalVerdict.PASS, SignalVerdict.FAIL):
                primary_result = result
                break

        if primary_result is None:
            primary_result = SignalResult(
                verdict=SignalVerdict.UNCERTAIN,
                confidence=0.0,
                signal_name="none",
                detail="All signals returned uncertain",
            )

        # Phase 2: Safety signals (always run, can override PASS -> FAIL)
        for safety_signal in self.safety_signals:
            safety_result = safety_signal.check(claim_text, segment_text, context)
            if safety_result.verdict == SignalVerdict.FAIL:
                return SignalResult(
                    verdict=SignalVerdict.FAIL,
                    confidence=safety_result.confidence,
                    signal_name=safety_result.signal_name,
                    detail=(
                        f"Safety override ({safety_result.signal_name}): "
                        f"{safety_result.detail}; "
                        f"overrode {primary_result.signal_name}"
                    ),
                )

        return primary_result

    def _verify_with_nli(
        self,
        claims: dict[str, str],
        segment_texts: list[str],
        attributions: dict[str, list[int]],
        candidates: dict[str, list[int]],
    ) -> list[VerificationResult]:
        """Stage 2 fallback: NLI entailment verification.

        Kept as fallback for when no signals are configured.
        """
        results: list[VerificationResult] = []

        for key, claim_text in claims.items():
            attributed_ids = attributions.get(key, [])
            candidate_ids = candidates.get(key, [])

            attributed_set = set(attributed_ids)
            extra_candidate_ids = [c for c in candidate_ids if c not in attributed_set]
            all_ids_to_check = list(attributed_ids) + extra_candidate_ids

            if not all_ids_to_check:
                results.append(
                    VerificationResult(
                        claim_key=key,
                        original_segment_ids=attributed_ids,
                    )
                )
                continue

            pairs = [
                (segment_texts[sid], claim_text)
                for sid in all_ids_to_check
                if sid < len(segment_texts)
            ]
            valid_ids = [sid for sid in all_ids_to_check if sid < len(segment_texts)]

            if not pairs:
                results.append(
                    VerificationResult(
                        claim_key=key,
                        original_segment_ids=attributed_ids,
                    )
                )
                continue

            nli_results = self.nli_service.classify_batch(pairs)

            max_attributed_score = 0.0
            for sid, nli_result in zip(valid_ids, nli_results, strict=True):
                if sid in attributed_set:
                    max_attributed_score = max(max_attributed_score, nli_result.entailment_score)

            possible_matches: list[int] = []
            for sid, nli_result in zip(valid_ids, nli_results, strict=True):
                if (
                    sid not in attributed_set
                    and nli_result.entailment_score > _POSSIBLE_MATCH_THRESHOLD
                ):
                    possible_matches.append(sid)

            confidence_score = max_attributed_score
            confidence_level = _score_to_level(confidence_score)

            if not attributed_ids:
                confidence_level = "unverified"

            results.append(
                VerificationResult(
                    claim_key=key,
                    original_segment_ids=attributed_ids,
                    confidence_score=confidence_score,
                    confidence_level=confidence_level,
                    possible_match_segment_ids=possible_matches,
                    signal_used="nli",
                )
            )

        return results


def _interleave(a: list[int], b: list[int]) -> list[int]:
    """Interleave two lists, taking one element from each alternately."""
    result: list[int] = []
    i, j = 0, 0
    while i < len(a) or j < len(b):
        if i < len(a):
            result.append(a[i])
            i += 1
        if j < len(b):
            result.append(b[j])
            j += 1
    return result
