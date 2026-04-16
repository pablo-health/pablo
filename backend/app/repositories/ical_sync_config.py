# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""iCal feed URL configuration repository interface and dataclass.

HIPAA Compliance: iCal feed URLs contain embedded tokens that grant
unauthenticated read access to therapist schedules (which may contain PHI).
URLs are encrypted at rest with AES-256-GCM before storage.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from typing import Any


@dataclass
class ICalSyncConfig:
    """Stored iCal feed configuration for a user + EHR system pair."""

    user_id: str
    ehr_system: str  # "simplepractice" | "sessions_health"
    encrypted_feed_url: str  # AES-256-GCM encrypted
    last_synced_at: datetime | None = None
    last_sync_error: str | None = None
    connected_at: datetime | None = None
    consecutive_error_count: int = 0

    @property
    def doc_id(self) -> str:
        return f"{self.user_id}_{self.ehr_system}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "user_id": self.user_id,
            "ehr_system": self.ehr_system,
            "encrypted_feed_url": self.encrypted_feed_url,
            "last_synced_at": self.last_synced_at,
            "last_sync_error": self.last_sync_error,
            "connected_at": self.connected_at,
            "consecutive_error_count": self.consecutive_error_count,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ICalSyncConfig:
        return cls(
            user_id=data["user_id"],
            ehr_system=data["ehr_system"],
            encrypted_feed_url=data["encrypted_feed_url"],
            last_synced_at=data.get("last_synced_at"),
            last_sync_error=data.get("last_sync_error"),
            connected_at=data.get("connected_at", ""),
            consecutive_error_count=data.get("consecutive_error_count", 0),
        )


class ICalSyncConfigRepository(ABC):
    """Abstract interface for iCal sync config storage."""

    @abstractmethod
    def get(self, user_id: str, ehr_system: str) -> ICalSyncConfig | None:
        pass

    @abstractmethod
    def list_by_user(self, user_id: str) -> list[ICalSyncConfig]:
        pass

    @abstractmethod
    def list_all(self) -> list[ICalSyncConfig]:
        pass

    @abstractmethod
    def save(self, config: ICalSyncConfig) -> None:
        pass

    @abstractmethod
    def delete(self, user_id: str, ehr_system: str) -> bool:
        pass

    @abstractmethod
    def update_sync_status(
        self, user_id: str, ehr_system: str, *, error: str | None = None
    ) -> None:
        pass
