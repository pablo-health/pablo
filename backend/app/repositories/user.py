# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""User repository implementations."""

from abc import ABC, abstractmethod
from datetime import UTC, datetime
from typing import Any

from ..models import User, UserPreferences


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
            created_at=datetime.now(UTC).isoformat().replace("+00:00", "Z"),
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


class FirestoreUserRepository(UserRepository):
    """Firestore implementation of UserRepository."""

    def __init__(self, db: Any) -> None:
        self.db = db
        self.collection = db.collection("users")

    def get(self, user_id: str) -> User | None:
        doc = self.collection.document(user_id).get()
        if doc.exists:
            return User.from_dict(doc.to_dict())
        return None

    def update(self, user: User) -> User:
        self.collection.document(user.id).set(user.to_dict())
        return user

    def list_all(self) -> list[User]:
        return [User.from_dict(doc.to_dict()) for doc in self.collection.stream()]

    def get_preferences(self, user_id: str) -> UserPreferences:
        doc = self.db.collection("user_preferences").document(user_id).get()
        if doc.exists:
            return UserPreferences(**doc.to_dict())
        return UserPreferences()

    def save_preferences(self, user_id: str, prefs: UserPreferences) -> UserPreferences:
        self.db.collection("user_preferences").document(user_id).set(prefs.model_dump())
        return prefs
