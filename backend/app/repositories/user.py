# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""User repository implementations."""

from abc import ABC, abstractmethod
from typing import Any

from ..models import User, UserPreferences
from ..utcnow import utc_now


class UserRepository(ABC):
    """Abstract base class for user data access."""

    @abstractmethod
    def get(self, user_id: str) -> User | None:
        """Get user by ID."""
        pass

    @abstractmethod
    def update(self, user: User) -> User:
        """Update an existing user."""
        pass

    @abstractmethod
    def list_all(self) -> list[User]:
        """List all users."""
        pass

    @abstractmethod
    def get_preferences(self, user_id: str) -> UserPreferences:
        """Get user preferences, returning defaults if none saved."""
        pass

    @abstractmethod
    def get_preferences_many(self, user_ids: list[str]) -> dict[str, UserPreferences]:
        """Bulk variant of :meth:`get_preferences`.

        Returns a dict keyed by every id in ``user_ids``; ids with no saved
        row map to default ``UserPreferences()``. One query instead of one
        per user — the sync-scheduler dispatch loop fans out across every
        user with a connected calendar.
        """
        pass

    @abstractmethod
    def save_preferences(self, user_id: str, prefs: UserPreferences) -> UserPreferences:
        """Save user preferences (full replace)."""
        pass

    @abstractmethod
    def update_preferences(self, user_id: str, changes: dict[str, Any]) -> UserPreferences:
        """Merge ``changes`` into the saved preferences as one atomic write.

        Keys not in ``changes`` are left as they are, including keys another
        writer saved a moment ago. A key whose value is ``None`` is removed,
        so it reads back as its default. ``changes`` must already be valid —
        see :func:`app.models.user.preference_changes`.
        """
        pass


class InMemoryUserRepository(UserRepository):
    """In-memory implementation of UserRepository for testing and development."""

    def __init__(self) -> None:
        self._users: dict[str, User] = {}
        self._preferences: dict[str, UserPreferences] = {}
        self._users["user123"] = User(
            id="user123",
            email="test@example.com",
            name="Test Therapist",
            created_at=utc_now(),
            title="Dr.",
            credentials="PhD, LMFT",
        )

    def get(self, user_id: str) -> User | None:
        return self._users.get(user_id)

    def update(self, user: User) -> User:
        self._users[user.id] = user
        return user

    def list_all(self) -> list[User]:
        return list(self._users.values())

    def get_preferences(self, user_id: str) -> UserPreferences:
        return self._preferences.get(user_id, UserPreferences())

    def get_preferences_many(self, user_ids: list[str]) -> dict[str, UserPreferences]:
        return {uid: self._preferences.get(uid, UserPreferences()) for uid in user_ids}

    def save_preferences(self, user_id: str, prefs: UserPreferences) -> UserPreferences:
        self._preferences[user_id] = prefs
        return prefs

    def update_preferences(self, user_id: str, changes: dict[str, Any]) -> UserPreferences:
        saved = self.get_preferences(user_id).model_dump()
        for key, value in changes.items():
            if value is None:
                saved.pop(key, None)
            else:
                saved[key] = value
        merged = UserPreferences(**saved)
        self._preferences[user_id] = merged
        return merged
