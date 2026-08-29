# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Real-Postgres tests for booking-link soft-delete.

The unit suite (``tests/test_public_booking_routes.py``) proves the
tombstone contract against the in-memory repository; these tests prove
the same contract against ``platform.booking_links``: a deleted slug's
``UNIQUE`` constraint still fires a real 23505, and ``deleted_at`` lands
in the row.
"""

from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest
from app.db import DEFAULT_PRACTICE_SCHEMA, PLATFORM_SCHEMA
from app.db.platform_models import PlatformBase
from app.models.booking_link import BookingLink
from app.repositories.booking_link import SlugTakenError
from app.repositories.postgres.booking_link import PostgresBookingLinkRepository
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

if TYPE_CHECKING:
    from collections.abc import Iterator

    from sqlalchemy.engine import Engine
    from sqlalchemy.orm import Session


@pytest.fixture(scope="module")
def engine() -> Iterator[Engine]:
    db_url = os.environ["DATABASE_URL"]
    eng = create_engine(db_url, pool_pre_ping=True)
    with eng.begin() as conn:
        conn.execute(text(f"CREATE SCHEMA IF NOT EXISTS {PLATFORM_SCHEMA}"))
        conn.execute(text(f"CREATE SCHEMA IF NOT EXISTS {DEFAULT_PRACTICE_SCHEMA}"))
        conn.execute(
            text(f"SET search_path = {DEFAULT_PRACTICE_SCHEMA}, {PLATFORM_SCHEMA}, public")
        )
        PlatformBase.metadata.create_all(conn)
    yield eng
    eng.dispose()


@pytest.fixture
def pg_session(engine: Engine) -> Iterator[Session]:
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    session = factory()
    session.execute(text("TRUNCATE TABLE platform.booking_links"))
    session.commit()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


def _link(*, slug: str, user_id: str) -> BookingLink:
    now = datetime(2026, 1, 1, tzinfo=UTC)
    return BookingLink(
        id=str(uuid.uuid4()),
        slug=slug,
        user_id=user_id,
        practice_id=None,
        host_name="Test Therapist",
        title="Intro call",
        description="A get-to-know-you call.",
        duration_minutes=30,
        session_type="individual",
        is_active=True,
        created_at=now,
        updated_at=now,
    )


def test_deleted_slug_cannot_be_reclaimed(pg_session: Session) -> None:
    repo = PostgresBookingLinkRepository(pg_session)
    owner_id = str(uuid.uuid4())
    link = repo.create(_link(slug="intro-call", user_id=owner_id))
    pg_session.commit()

    assert repo.delete(link.id, owner_id) is True
    pg_session.commit()

    with pytest.raises(SlugTakenError):
        repo.create(_link(slug="intro-call", user_id=str(uuid.uuid4())))

    assert repo.get_by_slug("intro-call") is None

    stored_deleted_at = pg_session.execute(
        text("SELECT deleted_at FROM platform.booking_links WHERE slug = :slug"),
        {"slug": "intro-call"},
    ).scalar_one()
    assert stored_deleted_at is not None
