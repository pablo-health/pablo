# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Who the practice owner has made a practice admin.

One row per admin, keyed by the clinician's user id. The owner is never a row:
ownership is on the practice, and an owner is an admin by being the owner.
Revoking deletes the row; the audit log is where the history is read.

The session is already pointed at one practice's schema, so no query here
carries a practice predicate.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from datetime import datetime


class PracticeAdminRepository(ABC):
    """Abstract base class for practice admin grants."""

    @abstractmethod
    def is_admin(self, user_id: str) -> bool:
        """Whether this clinician holds a grant."""

    @abstractmethod
    def list_admin_ids(self) -> set[str]:
        """Every clinician holding a grant."""

    @abstractmethod
    def grant(self, user_id: str, granted_by: str, granted_at: datetime) -> bool:
        """Record a grant. Returns ``False`` if the clinician already held one."""

    @abstractmethod
    def revoke(self, user_id: str) -> bool:
        """Remove a grant. Returns ``False`` if there was none."""


class InMemoryPracticeAdminRepository(PracticeAdminRepository):
    """In-memory implementation for tests."""

    def __init__(self) -> None:
        self._grants: dict[str, tuple[str, datetime]] = {}

    def is_admin(self, user_id: str) -> bool:
        return user_id in self._grants

    def list_admin_ids(self) -> set[str]:
        return set(self._grants)

    def grant(self, user_id: str, granted_by: str, granted_at: datetime) -> bool:
        if user_id in self._grants:
            return False
        self._grants[user_id] = (granted_by, granted_at)
        return True

    def revoke(self, user_id: str) -> bool:
        return self._grants.pop(user_id, None) is not None
