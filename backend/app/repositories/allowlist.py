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
    def add(self, email: str, added_by: str, *, practice_id: str) -> None:
        """Grant an email access to one practice, and map it there.

        The grant and the identity → practice mapping are deliberately one
        operation. Resolution runs on the login path for every user, so an
        email that is allowed but unmapped is an account that authenticates
        and then resolves to no practice — which reads as an empty account
        rather than as a misconfiguration.

        ``practice_id`` has no default because there is no correct practice
        to guess: an invitation is always into somebody's practice.
        """
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
        self._mappings: dict[str, str] = {}

    def is_allowed(self, email: str) -> bool:
        return email.lower() in self._entries

    def practice_for(self, email: str) -> str | None:
        """The practice this email resolves to, or None."""
        return self._mappings.get(email.lower())

    def add(self, email: str, added_by: str, *, practice_id: str) -> None:
        self._entries[email.lower()] = {
            "email": email.lower(),
            "added_by": added_by,
            "added_at": utc_now_iso(),
            "practice_id": practice_id,
        }
        # The in-memory double keeps the mapping too, so a test using it
        # cannot pass while the real repository would have left an email
        # allowed but unresolvable.
        self._mappings[email.lower()] = practice_id

    def remove(self, email: str) -> bool:
        self._mappings.pop(email.lower(), None)
        return self._entries.pop(email.lower(), None) is not None

    def list_all(self) -> list[dict[str, Any]]:
        return list(self._entries.values())
