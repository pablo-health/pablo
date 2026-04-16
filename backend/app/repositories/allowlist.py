# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Allowlist repository implementations."""

from abc import ABC, abstractmethod
from typing import Any

from ..utcnow import utc_now_iso


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
            "added_at": utc_now_iso(),
        }

    def remove(self, email: str) -> bool:
        return self._entries.pop(email.lower(), None) is not None

    def list_all(self) -> list[dict[str, Any]]:
        return list(self._entries.values())
