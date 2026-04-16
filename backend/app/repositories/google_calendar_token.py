# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Google Calendar OAuth token repository interface and dataclass."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from typing import Any


@dataclass
class GoogleCalendarTokenDoc:
    """Represents a stored Google Calendar token document."""

    user_id: str
    encrypted_tokens: str  # base64 AES-256-GCM encrypted
    calendar_id: str | None = None
    sync_token: str | None = None
    last_synced_at: datetime | None = None
    connected_at: datetime | None = None
    last_sync_error: str | None = None
    consecutive_error_count: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "user_id": self.user_id,
            "encrypted_tokens": self.encrypted_tokens,
            "calendar_id": self.calendar_id,
            "sync_token": self.sync_token,
            "last_synced_at": self.last_synced_at,
            "connected_at": self.connected_at,
            "last_sync_error": self.last_sync_error,
            "consecutive_error_count": self.consecutive_error_count,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> GoogleCalendarTokenDoc:
        return cls(
            user_id=data["user_id"],
            encrypted_tokens=data["encrypted_tokens"],
            calendar_id=data.get("calendar_id"),
            sync_token=data.get("sync_token"),
            last_synced_at=data.get("last_synced_at"),
            connected_at=data.get("connected_at"),
            last_sync_error=data.get("last_sync_error"),
            consecutive_error_count=data.get("consecutive_error_count", 0),
        )


class GoogleCalendarTokenRepository(ABC):
    """Abstract interface for Google Calendar token storage."""

    @abstractmethod
    def get(self, user_id: str) -> GoogleCalendarTokenDoc | None: ...

    @abstractmethod
    def list_all(self) -> list[GoogleCalendarTokenDoc]: ...

    @abstractmethod
    def save(self, token_doc: GoogleCalendarTokenDoc) -> None: ...

    @abstractmethod
    def update_sync_token(self, user_id: str, sync_token: str) -> None: ...

    @abstractmethod
    def delete(self, user_id: str) -> bool: ...

    @abstractmethod
    def exists(self, user_id: str) -> bool: ...
