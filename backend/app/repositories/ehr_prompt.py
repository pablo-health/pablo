# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""EHR prompt repository implementations."""

from __future__ import annotations

from abc import ABC, abstractmethod

from ..models.ehr_prompt import EhrPrompt
from ..utcnow import utc_now


class EhrPromptRepository(ABC):
    """Abstract base class for EHR prompt data access."""

    @abstractmethod
    def get(self, ehr_system: str) -> EhrPrompt | None:
        """Get prompt by EHR system name."""

    @abstractmethod
    def upsert(self, prompt: EhrPrompt) -> EhrPrompt:
        """Create or update an EHR prompt."""


class InMemoryEhrPromptRepository(EhrPromptRepository):
    """In-memory implementation for testing."""

    def __init__(self) -> None:
        self._prompts: dict[str, EhrPrompt] = {}

    def get(self, ehr_system: str) -> EhrPrompt | None:
        return self._prompts.get(ehr_system)

    def upsert(self, prompt: EhrPrompt) -> EhrPrompt:
        now = utc_now()
        prompt.updated_at = now
        self._prompts[prompt.ehr_system] = prompt
        return prompt

    def seed(self, prompt: EhrPrompt) -> None:
        """Seed test data without modifying timestamps."""
        self._prompts[prompt.ehr_system] = prompt
