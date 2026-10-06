# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Client AI-notes consent repository: append an answer, read them back.

There is deliberately no update and no delete. The record is a history —
changing an answer appends — so the interface offers nothing that could
rewrite one.

Access is the caller's to check before it gets here (the routes 404 a client
the clinician cannot see), and the table's row policy enforces the same
``has_patient_access`` rule underneath.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..models.client_ai_consent import AiConsentEvent


class ClientAiConsentRepository(ABC):
    @abstractmethod
    def append(self, event: AiConsentEvent) -> AiConsentEvent:
        """Write one answer."""

    @abstractmethod
    def list_for_patient(self, patient_id: str) -> list[AiConsentEvent]:
        """Every answer for this client, oldest first by ``recorded_at``."""


class InMemoryClientAiConsentRepository(ClientAiConsentRepository):
    """In-memory repository for unit tests."""

    def __init__(self) -> None:
        self.events: list[AiConsentEvent] = []

    def append(self, event: AiConsentEvent) -> AiConsentEvent:
        self.events.append(event.model_copy())
        return event

    def list_for_patient(self, patient_id: str) -> list[AiConsentEvent]:
        rows = [e.model_copy() for e in self.events if e.patient_id == patient_id]
        return sorted(rows, key=lambda e: e.recorded_at)
