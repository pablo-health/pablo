# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Tests for the OSS note-type authorizer (default allow-all + singleton wiring)."""

from __future__ import annotations

from datetime import datetime
from unittest.mock import MagicMock

from app.notes import NoteTypeAuthorizer, get_note_type_authorizer


def _user() -> MagicMock:
    user = MagicMock()
    user.id = "test-user-1"
    user.email = "test@example.com"
    user.created_at = datetime.fromisoformat("2024-01-01T00:00:00+00:00")
    return user


def test_default_authorizer_allows_any_note_type() -> None:
    """OSS default is allow-all — every note type is permitted for every user."""
    authorizer = NoteTypeAuthorizer()
    user = _user()

    assert authorizer.is_allowed(user, "soap") is True
    assert authorizer.is_allowed(user, "narrative") is True
    assert authorizer.is_allowed(user, "dap") is True
    assert authorizer.is_allowed(user, "anything-goes") is True


def test_get_note_type_authorizer_returns_singleton() -> None:
    """Repeated calls return the same instance so overlays can swap one object."""
    a = get_note_type_authorizer()
    b = get_note_type_authorizer()

    assert a is b
    assert isinstance(a, NoteTypeAuthorizer)
