# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""iCal client identifier to Pablo patient mapping repository interface and dataclass.

Maps EHR-specific client identifiers (SimplePractice initials like "J.A.",
Sessions Health codes like "SH00001") to Pablo patient IDs. Persisted so
future syncs auto-resolve known clients.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from typing import Any


@dataclass
class ICalClientMapping:
    """Maps an iCal client identifier to a Pablo patient."""

    user_id: str
    ehr_system: str
    client_identifier: str  # "J.A." or "SH00001"
    patient_id: str
    created_at: datetime | None = None

    @property
    def doc_id(self) -> str:
        return f"{self.user_id}_{self.ehr_system}_{self.client_identifier}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "user_id": self.user_id,
            "ehr_system": self.ehr_system,
            "client_identifier": self.client_identifier,
            "patient_id": self.patient_id,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ICalClientMapping:
        return cls(
            user_id=data["user_id"],
            ehr_system=data["ehr_system"],
            client_identifier=data["client_identifier"],
            patient_id=data["patient_id"],
            created_at=data.get("created_at", ""),
        )


class ICalClientMappingRepository(ABC):
    """Abstract interface for iCal client mapping storage."""

    @abstractmethod
    def get(
        self, user_id: str, ehr_system: str, client_identifier: str
    ) -> ICalClientMapping | None:
        pass

    @abstractmethod
    def list_by_user(self, user_id: str) -> list[ICalClientMapping]:
        pass

    @abstractmethod
    def list_by_source(self, user_id: str, ehr_system: str) -> list[ICalClientMapping]:
        pass

    @abstractmethod
    def save(self, mapping: ICalClientMapping) -> None:
        pass

    @abstractmethod
    def delete(self, user_id: str, ehr_system: str, client_identifier: str) -> bool:
        pass
