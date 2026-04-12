# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Firestore repository for encrypted Google Calendar OAuth tokens."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from ..utcnow import utc_now_iso

logger = logging.getLogger(__name__)

COLLECTION = "google_calendar_tokens"


@dataclass
class GoogleCalendarTokenDoc:
    """Represents a stored Google Calendar token document."""

    user_id: str
    encrypted_tokens: str  # base64 AES-256-GCM encrypted
    calendar_id: str | None = None
    sync_token: str | None = None
    last_synced_at: str | None = None
    connected_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "user_id": self.user_id,
            "encrypted_tokens": self.encrypted_tokens,
            "calendar_id": self.calendar_id,
            "sync_token": self.sync_token,
            "last_synced_at": self.last_synced_at,
            "connected_at": self.connected_at,
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
        )


class GoogleCalendarTokenRepository:
    """Stores and retrieves encrypted Google Calendar OAuth tokens in Firestore.

    HIPAA: Tokens are encrypted with AES-256-GCM before reaching this layer.
    This repository stores only the encrypted blob — it never sees plaintext tokens.
    """

    def __init__(self, db: Any) -> None:
        self._db = db
        self._collection = db.collection(COLLECTION)

    def get(self, user_id: str) -> GoogleCalendarTokenDoc | None:
        doc = self._collection.document(user_id).get()
        if not doc.exists:
            return None
        return GoogleCalendarTokenDoc.from_dict(doc.to_dict())

    def save(self, token_doc: GoogleCalendarTokenDoc) -> None:
        self._collection.document(token_doc.user_id).set(token_doc.to_dict())

    def update_sync_token(self, user_id: str, sync_token: str) -> None:
        now = utc_now_iso()
        self._collection.document(user_id).update(
            {
                "sync_token": sync_token,
                "last_synced_at": now,
            }
        )

    def delete(self, user_id: str) -> bool:
        doc = self._collection.document(user_id).get()
        if not doc.exists:
            return False
        self._collection.document(user_id).delete()
        return True

    def exists(self, user_id: str) -> bool:
        doc = self._collection.document(user_id).get()
        return doc.exists  # type: ignore[no-any-return]
