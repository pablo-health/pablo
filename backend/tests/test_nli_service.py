# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Tests for NLI verification service."""

import os

os.environ["ENVIRONMENT"] = "development"

import pytest
from app.services.nli_service import DeBERTaNLIService, MockNLIService, NLIResult
from app.settings import Settings


class TestNLIResult:
    """Tests for the NLIResult dataclass."""

    def test_result_fields(self) -> None:
        result = NLIResult(
            label="entailment",
            entailment_score=0.9,
            contradiction_score=0.05,
            neutral_score=0.05,
        )
        assert result.label == "entailment"
        assert result.entailment_score == 0.9
        assert result.contradiction_score == 0.05
        assert result.neutral_score == 0.05

    def test_scores_sum_to_one(self) -> None:
        result = NLIResult(
            label="neutral",
            entailment_score=0.2,
            contradiction_score=0.3,
            neutral_score=0.5,
        )
        total = result.entailment_score + result.contradiction_score + result.neutral_score
        assert total == pytest.approx(1.0)


class TestMockNLIService:
    """Tests for the mock NLI service."""

    def test_default_returns_entailment(self) -> None:
        service = MockNLIService()
        result = service.classify("some premise", "some hypothesis")
        assert result.label == "entailment"
        assert result.entailment_score == 0.9

    def test_configurable_default_label(self) -> None:
        service = MockNLIService(default_label="contradiction", default_score=0.85)
        result = service.classify("premise", "hypothesis")
        assert result.label == "contradiction"
        assert result.contradiction_score == 0.85

    def test_custom_pair_response(self) -> None:
        service = MockNLIService()
        custom = NLIResult(
            label="contradiction",
            entailment_score=0.1,
            contradiction_score=0.8,
            neutral_score=0.1,
        )
        service.set_response("The sky is blue", "The sky is green", custom)

        result = service.classify("The sky is blue", "The sky is green")
        assert result.label == "contradiction"
        assert result.contradiction_score == 0.8

    def test_custom_pair_does_not_affect_other_pairs(self) -> None:
        service = MockNLIService()
        custom = NLIResult(
            label="contradiction",
            entailment_score=0.1,
            contradiction_score=0.8,
            neutral_score=0.1,
        )
        service.set_response("specific premise", "specific hypothesis", custom)

        # Different pair should get default
        result = service.classify("other premise", "other hypothesis")
        assert result.label == "entailment"

    def test_batch_processing(self) -> None:
        service = MockNLIService()
        pairs = [
            ("premise 1", "hypothesis 1"),
            ("premise 2", "hypothesis 2"),
            ("premise 3", "hypothesis 3"),
        ]
        results = service.classify_batch(pairs)
        assert len(results) == 3
        assert all(r.label == "entailment" for r in results)

    def test_batch_with_custom_responses(self) -> None:
        service = MockNLIService()
        contradiction = NLIResult(
            label="contradiction",
            entailment_score=0.05,
            contradiction_score=0.9,
            neutral_score=0.05,
        )
        service.set_response("A", "B", contradiction)

        pairs = [("A", "B"), ("C", "D")]
        results = service.classify_batch(pairs)
        assert results[0].label == "contradiction"
        assert results[1].label == "entailment"

    def test_empty_batch_raises(self) -> None:
        service = MockNLIService()
        with pytest.raises(ValueError, match="empty"):
            service.classify_batch([])

    def test_default_scores_sum_to_one(self) -> None:
        service = MockNLIService(default_label="neutral", default_score=0.7)
        result = service.classify("p", "h")
        total = result.entailment_score + result.contradiction_score + result.neutral_score
        assert total == pytest.approx(1.0)

    def test_all_three_labels(self) -> None:
        """Verify each label can be configured as default."""
        for label in ["entailment", "contradiction", "neutral"]:
            service = MockNLIService(default_label=label, default_score=0.8)
            result = service.classify("p", "h")
            assert result.label == label
            assert getattr(result, f"{label}_score") == 0.8


class TestDeBERTaNLIServiceInit:
    """Tests for DeBERTaNLIService initialization."""

    def test_default_model_name(self) -> None:
        service = DeBERTaNLIService()
        assert service.model_name == "cross-encoder/nli-deberta-v3-xsmall"

    def test_custom_model_name(self) -> None:
        service = DeBERTaNLIService(model_name="/path/to/finetuned-model")
        assert service.model_name == "/path/to/finetuned-model"

    def test_model_not_loaded_until_first_use(self) -> None:
        service = DeBERTaNLIService(model_name="nonexistent-model")
        assert service._model is None


class TestSettingsNLIModelPath:
    """Tests for the nli_model_path setting."""

    def test_default_nli_model_path(self) -> None:
        s = Settings(environment="development")
        assert s.nli_model_path == "cross-encoder/nli-deberta-v3-xsmall"

    def test_custom_nli_model_path(self) -> None:
        s = Settings(environment="development", nli_model_path="/custom/model")
        assert s.nli_model_path == "/custom/model"
