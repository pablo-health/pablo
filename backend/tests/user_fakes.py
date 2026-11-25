# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""A signed-in clinician for route tests that stand in a practice row."""

from __future__ import annotations

from datetime import UTC, datetime

from app.models import User


def clinician(user_id: str, email: str) -> User:
    return User(
        id=user_id,
        email=email,
        name="Test Clinician",
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
        baa_accepted_at=datetime(2026, 1, 1, tzinfo=UTC),
        baa_version="2024-01-01",
    )
