# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""EHR prompt repository implementations."""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from ..models.ehr_prompt import EhrPrompt

if TYPE_CHECKING:
    from google.cloud.firestore import Client as FirestoreClient

EHR_PROMPTS_COLLECTION = "ehr_prompts"


class EhrPromptRepository(ABC):
    """Abstract base class for EHR prompt data access."""

    @abstractmethod
    def get(self, ehr_system: str) -> EhrPrompt | None:
        """Get prompt by EHR system name."""

    @abstractmethod
    def upsert(self, prompt: EhrPrompt) -> EhrPrompt:
        """Create or update an EHR prompt."""


class FirestoreEhrPromptRepository(EhrPromptRepository):
    """Firestore implementation of EhrPromptRepository."""

    def __init__(self, db: FirestoreClient) -> None:
        self._db = db

    def _collection(self) -> Any:
        return self._db.collection(EHR_PROMPTS_COLLECTION)

    def get(self, ehr_system: str) -> EhrPrompt | None:
        doc = self._collection().document(ehr_system).get()
        if not doc.exists:
            return None
        data = doc.to_dict()
        data["ehr_system"] = doc.id
        return EhrPrompt.from_dict(data)

    def upsert(self, prompt: EhrPrompt) -> EhrPrompt:
        now = datetime.now(UTC).isoformat().replace("+00:00", "Z")
        prompt.updated_at = now
        self._collection().document(prompt.ehr_system).set(prompt.to_dict())
        return prompt


class InMemoryEhrPromptRepository(EhrPromptRepository):
    """In-memory implementation for testing."""

    def __init__(self) -> None:
        self._prompts: dict[str, EhrPrompt] = {}

    def get(self, ehr_system: str) -> EhrPrompt | None:
        return self._prompts.get(ehr_system)

    def upsert(self, prompt: EhrPrompt) -> EhrPrompt:
        now = datetime.now(UTC).isoformat().replace("+00:00", "Z")
        prompt.updated_at = now
        self._prompts[prompt.ehr_system] = prompt
        return prompt

    def seed(self, prompt: EhrPrompt) -> None:
        """Seed test data without modifying timestamps."""
        self._prompts[prompt.ehr_system] = prompt
