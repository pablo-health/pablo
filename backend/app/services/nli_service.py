# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Natural Language Inference service for verifying SOAP claim attribution."""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

# DeBERTa NLI models use this label ordering
NLI_LABELS = ["contradiction", "entailment", "neutral"]


@dataclass
class NLIResult:
    """Result of NLI classification for a premise-hypothesis pair."""

    label: str  # "entailment" | "contradiction" | "neutral"
    entailment_score: float  # probability of entailment (0.0-1.0)
    contradiction_score: float
    neutral_score: float


class NLIService(ABC):
    """Abstract interface for Natural Language Inference."""

    @abstractmethod
    def classify(self, premise: str, hypothesis: str) -> NLIResult:
        """Classify the relationship between premise and hypothesis.

        Args:
            premise: The source text (e.g., transcript segment).
            hypothesis: The claim to verify (e.g., SOAP note sentence).

        Returns:
            NLI classification result with label and scores.
        """
        ...

    @abstractmethod
    def classify_batch(self, pairs: list[tuple[str, str]]) -> list[NLIResult]:
        """Batch classify multiple premise-hypothesis pairs.

        Args:
            pairs: List of (premise, hypothesis) tuples.

        Returns:
            List of NLI classification results.
        """
        ...


class DeBERTaNLIService(NLIService):
    """DeBERTa-v3-xsmall cross-encoder NLI via ONNX Runtime.

    Uses sentence_transformers.CrossEncoder with backend="onnx" for fast
    CPU inference. The model (~22MB) downloads once to ~/.cache/huggingface/
    on first use.
    """

    def __init__(self, model_name: str = "cross-encoder/nli-deberta-v3-xsmall") -> None:
        self.model_name = model_name
        self._model: Any = None

    def _get_model(self) -> Any:
        """Lazily load the cross-encoder model."""
        if self._model is None:
            try:
                from sentence_transformers import CrossEncoder

                self._model = CrossEncoder(
                    self.model_name,
                    backend="onnx",
                )
            except Exception as err:
                msg = f"Failed to load NLI model '{self.model_name}': {err}"
                raise RuntimeError(msg) from err
        return self._model

    def classify(self, premise: str, hypothesis: str) -> NLIResult:
        """Classify a single premise-hypothesis pair."""
        results = self.classify_batch([(premise, hypothesis)])
        return results[0]

    def classify_batch(self, pairs: list[tuple[str, str]]) -> list[NLIResult]:
        """Batch classify using the DeBERTa cross-encoder.

        Args:
            pairs: List of (premise, hypothesis) tuples.

        Returns:
            List of NLI classification results.

        Raises:
            ValueError: If pairs list is empty.
            RuntimeError: If model inference fails.
        """
        if not pairs:
            msg = "Cannot classify an empty list of pairs"
            raise ValueError(msg)

        try:
            model = self._get_model()
            # predict returns shape (n_pairs, 3) with softmax scores
            scores = model.predict(
                list(pairs),
                apply_softmax=True,
                convert_to_numpy=True,
            )

            results: list[NLIResult] = []
            for row in scores:
                label_idx = int(row.argmax())
                results.append(
                    NLIResult(
                        label=NLI_LABELS[label_idx],
                        contradiction_score=float(row[0]),
                        entailment_score=float(row[1]),
                        neutral_score=float(row[2]),
                    )
                )
            return results
        except (ValueError, RuntimeError):
            raise
        except Exception as err:
            msg = f"NLI inference failed: {err}"
            raise RuntimeError(msg) from err


class MockNLIService(NLIService):
    """Deterministic mock for testing.

    Returns configurable default results and supports per-pair overrides
    for testing specific scenarios.
    """

    def __init__(
        self,
        default_label: str = "entailment",
        default_score: float = 0.9,
    ) -> None:
        self.default_label = default_label
        self.default_score = default_score
        self._pair_responses: dict[tuple[str, str], NLIResult] = {}

    def set_response(
        self,
        premise: str,
        hypothesis: str,
        result: NLIResult,
    ) -> None:
        """Configure a specific response for a premise-hypothesis pair."""
        self._pair_responses[(premise, hypothesis)] = result

    def classify(self, premise: str, hypothesis: str) -> NLIResult:
        """Return configured result or default."""
        if (premise, hypothesis) in self._pair_responses:
            return self._pair_responses[(premise, hypothesis)]
        return self._make_default_result()

    def classify_batch(self, pairs: list[tuple[str, str]]) -> list[NLIResult]:
        """Batch classify using configured or default results."""
        if not pairs:
            msg = "Cannot classify an empty list of pairs"
            raise ValueError(msg)
        return [self.classify(p, h) for p, h in pairs]

    def _make_default_result(self) -> NLIResult:
        """Build an NLIResult from the default label and score."""
        remainder = (1.0 - self.default_score) / 2.0
        scores = {
            "entailment": remainder,
            "contradiction": remainder,
            "neutral": remainder,
        }
        scores[self.default_label] = self.default_score
        return NLIResult(
            label=self.default_label,
            entailment_score=scores["entailment"],
            contradiction_score=scores["contradiction"],
            neutral_score=scores["neutral"],
        )
