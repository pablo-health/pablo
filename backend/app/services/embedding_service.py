# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Embedding service for generating text vector representations."""

from __future__ import annotations

import hashlib
import logging
import math
import struct
from abc import ABC, abstractmethod
from typing import Any

logger = logging.getLogger(__name__)


def cosine_similarity(vec_a: list[float], vec_b: list[float]) -> float:
    """Compute cosine similarity between two vectors.

    Returns a value in [-1.0, 1.0] where 1.0 means identical direction,
    0.0 means orthogonal, and -1.0 means opposite direction.

    Raises:
        ValueError: If vectors have different lengths or are empty.
    """
    if len(vec_a) != len(vec_b):
        msg = f"Vector length mismatch: {len(vec_a)} vs {len(vec_b)}"
        raise ValueError(msg)
    if not vec_a:
        msg = "Cannot compute cosine similarity of empty vectors"
        raise ValueError(msg)

    dot = sum(a * b for a, b in zip(vec_a, vec_b, strict=True))
    norm_a = math.sqrt(sum(a * a for a in vec_a))
    norm_b = math.sqrt(sum(b * b for b in vec_b))

    if not norm_a or not norm_b:
        return 0.0

    return dot / (norm_a * norm_b)


class EmbeddingService(ABC):
    """Abstract interface for text embedding generation."""

    @abstractmethod
    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        """Embed a batch of texts. Returns list of embedding vectors."""
        ...


class GoogleEmbeddingService(EmbeddingService):
    """Google text-embedding-004 via Vertex AI.

    Uses the google-genai SDK with Vertex AI backend. Authentication is
    handled via Application Default Credentials (ADC) or explicit credentials
    passed to the client.
    """

    def __init__(self, model_name: str = "text-embedding-004") -> None:
        self.model_name = model_name
        self._client: Any = None

    def _get_client(self) -> Any:
        """Lazily initialize the google.genai client."""
        if self._client is None:
            from google import genai

            self._client = genai.Client(vertexai=True)
        return self._client

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        """Batch embed texts using Google text-embedding-004.

        Args:
            texts: List of text strings to embed.

        Returns:
            List of embedding vectors, one per input text.

        Raises:
            ValueError: If texts list is empty.
            RuntimeError: If the embedding API call fails.
        """
        if not texts:
            msg = "Cannot embed an empty list of texts"
            raise ValueError(msg)

        try:
            from google.genai import types

            client = self._get_client()
            response = client.models.embed_content(
                model=self.model_name,
                contents=texts,
                config=types.EmbedContentConfig(output_dimensionality=768),
            )
            return [list(emb.values) for emb in response.embeddings]
        except ImportError as err:
            msg = "google-genai package is required for GoogleEmbeddingService"
            raise RuntimeError(msg) from err
        except Exception as err:
            logger.exception("Embedding API call failed")
            msg = f"Embedding API call failed: {err}"
            raise RuntimeError(msg) from err


class MockEmbeddingService(EmbeddingService):
    """Deterministic mock for testing. Returns consistent vectors based on text hash.

    The same input text always produces the same vector, while different texts
    produce different vectors. Vectors are unit-normalized.
    """

    def __init__(self, dimensions: int = 768) -> None:
        self.dimensions = dimensions

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        """Generate deterministic embedding vectors from text content.

        Args:
            texts: List of text strings to embed.

        Returns:
            List of deterministic embedding vectors.

        Raises:
            ValueError: If texts list is empty.
        """
        if not texts:
            msg = "Cannot embed an empty list of texts"
            raise ValueError(msg)

        return [self._hash_to_vector(text) for text in texts]

    def _hash_to_vector(self, text: str) -> list[float]:
        """Generate a deterministic unit vector from text via SHA-256 seeded expansion."""
        # Use SHA-256 to seed a deterministic sequence of floats
        floats: list[float] = []
        chunk_index = 0
        while len(floats) < self.dimensions:
            digest = hashlib.sha256(f"{text}:{chunk_index}".encode()).digest()
            # Unpack 8 floats (32 bytes) from each digest
            for i in range(0, 32, 4):
                if len(floats) >= self.dimensions:
                    break
                # Convert 4 bytes to a float in [-1, 1]
                raw = struct.unpack(">I", digest[i : i + 4])[0]
                floats.append((raw / 2147483647.5) - 1.0)
            chunk_index += 1

        # Normalize to unit vector
        norm = math.sqrt(sum(f * f for f in floats))
        if norm > 0:
            floats = [f / norm for f in floats]
        return floats
