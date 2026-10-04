# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Clinicians' choice to let a session title naming one client book on its own."""

from __future__ import annotations

from app.models.user import UserPreferences
from app.repositories.user import InMemoryUserRepository


def choosing(*, books: bool, user_ids: tuple[str, ...]) -> InMemoryUserRepository:
    """A user store in which these clinicians chose ``books``; anyone else has the default."""
    users = InMemoryUserRepository()
    for user_id in user_ids:
        users.save_preferences(user_id, UserPreferences(book_sessions_named_in_title=books))
    return users
