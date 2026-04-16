# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""User repository implementations."""

from abc import ABC, abstractmethod

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
    def save_preferences(self, user_id: str, prefs: UserPreferences) -> UserPreferences:
        """Save user preferences (full replace)."""
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

    def save_preferences(self, user_id: str, prefs: UserPreferences) -> UserPreferences:
        self._preferences[user_id] = prefs
        return prefs
