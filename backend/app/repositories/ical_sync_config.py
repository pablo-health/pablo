# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Firestore repository for encrypted iCal feed URL configuration.

HIPAA Compliance: iCal feed URLs contain embedded tokens that grant
unauthenticated read access to therapist schedules (which may contain PHI).
URLs are encrypted at rest with AES-256-GCM before storage.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

logger = logging.getLogger(__name__)

COLLECTION = "ical_sync_configs"


def _now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


@dataclass
class ICalSyncConfig:
    """Stored iCal feed configuration for a user + EHR system pair."""

    user_id: str
    ehr_system: str  # "simplepractice" | "sessions_health"
    encrypted_feed_url: str  # AES-256-GCM encrypted
    last_synced_at: str | None = None
    last_sync_error: str | None = None
    connected_at: str = ""

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
        )


class ICalSyncConfigRepository:
    """Stores encrypted iCal feed URLs in Firestore.

    One document per user per EHR system, keyed as {user_id}_{ehr_system}.
    Supports therapists connected to multiple EHR systems simultaneously.
    """

    def __init__(self, db: Any) -> None:
        self._db = db
        self._collection = db.collection(COLLECTION)

    def get(self, user_id: str, ehr_system: str) -> ICalSyncConfig | None:
        doc_id = f"{user_id}_{ehr_system}"
        doc = self._collection.document(doc_id).get()
        if not doc.exists:
            return None
        return ICalSyncConfig.from_dict(doc.to_dict())

    def list_by_user(self, user_id: str) -> list[ICalSyncConfig]:
        query = self._collection.where("user_id", "==", user_id)
        return [ICalSyncConfig.from_dict(doc.to_dict()) for doc in query.stream()]

    def save(self, config: ICalSyncConfig) -> None:
        self._collection.document(config.doc_id).set(config.to_dict())

    def delete(self, user_id: str, ehr_system: str) -> bool:
        doc_id = f"{user_id}_{ehr_system}"
        doc = self._collection.document(doc_id).get()
        if not doc.exists:
            return False
        self._collection.document(doc_id).delete()
        return True

    def update_sync_status(
        self,
        user_id: str,
        ehr_system: str,
        *,
        error: str | None = None,
    ) -> None:
        doc_id = f"{user_id}_{ehr_system}"
        self._collection.document(doc_id).update({
            "last_synced_at": _now(),
            "last_sync_error": error,
        })
