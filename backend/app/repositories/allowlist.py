# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Allowlist repository implementations."""

from abc import ABC, abstractmethod
from datetime import UTC, datetime
from typing import Any


class AllowlistRepository(ABC):
    """Abstract base class for email allowlist access."""

    @abstractmethod
    def is_allowed(self, email: str) -> bool:
        """Check if an email is in the allowlist."""
        pass

    @abstractmethod
    def add(self, email: str, added_by: str) -> None:
        """Add an email to the allowlist."""
        pass

    @abstractmethod
    def remove(self, email: str) -> bool:
        """Remove an email from the allowlist. Returns True if removed."""
        pass

    @abstractmethod
    def list_all(self) -> list[dict[str, Any]]:
        """List all allowlisted emails with metadata."""
        pass


class InMemoryAllowlistRepository(AllowlistRepository):
    """In-memory implementation of AllowlistRepository for testing."""

    def __init__(self) -> None:
        self._entries: dict[str, dict[str, Any]] = {}

    def is_allowed(self, email: str) -> bool:
        return email.lower() in self._entries

    def add(self, email: str, added_by: str) -> None:
        self._entries[email.lower()] = {
            "email": email.lower(),
            "added_by": added_by,
            "added_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        }

    def remove(self, email: str) -> bool:
        return self._entries.pop(email.lower(), None) is not None

    def list_all(self) -> list[dict[str, Any]]:
        return list(self._entries.values())


class FirestoreAllowlistRepository(AllowlistRepository):
    """Firestore implementation of AllowlistRepository."""

    def __init__(self, db: Any) -> None:
        self.db = db
        self.collection = db.collection("allowed_emails")

    def is_allowed(self, email: str) -> bool:
        doc = self.collection.document(email.lower()).get()
        return bool(doc.exists)

    def add(self, email: str, added_by: str) -> None:
        self.collection.document(email.lower()).set(
            {
                "email": email.lower(),
                "added_by": added_by,
                "added_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            }
        )

    def remove(self, email: str) -> bool:
        doc_ref = self.collection.document(email.lower())
        if doc_ref.get().exists:
            doc_ref.delete()
            return True
        return False

    def list_all(self) -> list[dict[str, Any]]:
        return [doc.to_dict() for doc in self.collection.stream()]
