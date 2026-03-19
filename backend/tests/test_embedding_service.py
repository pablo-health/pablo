# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Tests for embedding service."""

import math
import os

os.environ["ENVIRONMENT"] = "development"

import pytest
from app.services.embedding_service import (
    MockEmbeddingService,
    cosine_similarity,
)


class TestCosinesimilarity:
    """Tests for the cosine_similarity utility function."""

    def test_identical_vectors_return_one(self) -> None:
        vec = [1.0, 2.0, 3.0]
        assert cosine_similarity(vec, vec) == pytest.approx(1.0)

    def test_opposite_vectors_return_negative_one(self) -> None:
        vec_a = [1.0, 0.0, 0.0]
        vec_b = [-1.0, 0.0, 0.0]
        assert cosine_similarity(vec_a, vec_b) == pytest.approx(-1.0)

    def test_orthogonal_vectors_return_zero(self) -> None:
        vec_a = [1.0, 0.0]
        vec_b = [0.0, 1.0]
        assert cosine_similarity(vec_a, vec_b) == pytest.approx(0.0)

    def test_length_mismatch_raises(self) -> None:
        with pytest.raises(ValueError, match="length mismatch"):
            cosine_similarity([1.0, 2.0], [1.0])

    def test_empty_vectors_raise(self) -> None:
        with pytest.raises(ValueError, match="empty vectors"):
            cosine_similarity([], [])

    def test_zero_vector_returns_zero(self) -> None:
        assert cosine_similarity([0.0, 0.0], [1.0, 2.0]) == 0.0

    def test_known_angle(self) -> None:
        # 45-degree angle: cos(45) ~ 0.707
        vec_a = [1.0, 0.0]
        vec_b = [1.0, 1.0]
        expected = 1.0 / math.sqrt(2.0)
        assert cosine_similarity(vec_a, vec_b) == pytest.approx(expected)


class TestMockEmbeddingService:
    """Tests for the deterministic mock embedding service."""

    def test_consistent_vectors_for_same_input(self) -> None:
        service = MockEmbeddingService()
        result1 = service.embed_texts(["hello world"])
        result2 = service.embed_texts(["hello world"])
        assert result1 == result2

    def test_different_vectors_for_different_input(self) -> None:
        service = MockEmbeddingService()
        [vec_a] = service.embed_texts(["hello"])
        [vec_b] = service.embed_texts(["goodbye"])
        assert vec_a != vec_b

    def test_batch_returns_correct_count(self) -> None:
        service = MockEmbeddingService()
        texts = ["one", "two", "three", "four"]
        results = service.embed_texts(texts)
        assert len(results) == 4

    def test_vector_dimensions(self) -> None:
        service = MockEmbeddingService(dimensions=128)
        [vec] = service.embed_texts(["test"])
        assert len(vec) == 128

    def test_vectors_are_unit_normalized(self) -> None:
        service = MockEmbeddingService()
        [vec] = service.embed_texts(["test normalization"])
        norm = math.sqrt(sum(v * v for v in vec))
        assert norm == pytest.approx(1.0, abs=1e-6)

    def test_empty_texts_raises(self) -> None:
        service = MockEmbeddingService()
        with pytest.raises(ValueError, match="empty"):
            service.embed_texts([])

    def test_similar_texts_have_different_embeddings(self) -> None:
        service = MockEmbeddingService()
        [vec_a] = service.embed_texts(["The patient feels anxious"])
        [vec_b] = service.embed_texts(["The patient feels anxious."])
        # Same content with minor difference should produce different vectors
        assert vec_a != vec_b

    def test_cosine_similarity_of_same_text_is_one(self) -> None:
        service = MockEmbeddingService()
        [vec] = service.embed_texts(["identical text"])
        assert cosine_similarity(vec, vec) == pytest.approx(1.0)

    def test_batch_consistency(self) -> None:
        """Batch embedding should produce same vectors as individual calls."""
        service = MockEmbeddingService()
        texts = ["alpha", "beta"]
        batch_results = service.embed_texts(texts)
        individual_a = service.embed_texts(["alpha"])[0]
        individual_b = service.embed_texts(["beta"])[0]
        assert batch_results[0] == individual_a
        assert batch_results[1] == individual_b
