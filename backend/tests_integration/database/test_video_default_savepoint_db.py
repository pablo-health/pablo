# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""A video default that cannot be saved must not take the calendar connect with it.

The Google Calendar callback writes the clinician's token row, then sets Meet as
the video default in the same transaction. If that second write hits a database
error (a lock or statement timeout, a deadlock), Postgres aborts the whole
transaction, and the request's commit quietly becomes a rollback: the route
answers "connected" for a connection that was never stored. The callback runs
the default inside a savepoint so that only the default is lost.

The failure is forced, not hoped for: a second connection holds the clinician's
preferences row ``FOR UPDATE`` and the request side waits with a short
``lock_timeout``. Another clinician's preferences row stands in for the token
row: what matters is a write flushed earlier in the same transaction, and a
platform row avoids needing a practice schema. The no-savepoint case is kept to
show the failure really is being forced.
"""

from __future__ import annotations

import contextlib
import os
import uuid
from typing import TYPE_CHECKING

import pytest
from app.db.platform_models import PlatformUserPreferencesRow
from app.meeting_providers.meet import GoogleMeetProvider
from app.repositories.postgres.user import PostgresUserRepository
from app.routes.scheduling import _default_to_meet
from app.services.telehealth import MeetingProviderRegistry
from sqlalchemy import create_engine, delete, select, text
from sqlalchemy.orm import Session, sessionmaker

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

pytestmark = pytest.mark.skipif(
    os.environ.get("DATABASE_BACKEND") != "postgres",
    reason="Requires the Postgres integration database.",
)


@pytest.fixture(scope="module")
def sessions() -> Iterator[sessionmaker[Session]]:
    engine = create_engine(os.environ["DATABASE_URL"], pool_size=4)
    yield sessionmaker(bind=engine, expire_on_commit=False)
    engine.dispose()


@pytest.fixture
def users(sessions: sessionmaker[Session]) -> Iterator[tuple[str, str]]:
    """The clinician connecting, and the row standing in for their token."""
    clinician, stand_in = str(uuid.uuid4()), str(uuid.uuid4())
    with sessions.begin() as session:
        # A preferences row with no saved video default, so Meet is due.
        PostgresUserRepository(session).update_preferences(
            clinician, {"default_duration_minutes": 50}
        )
    yield clinician, stand_in
    with sessions.begin() as session:
        session.execute(
            delete(PlatformUserPreferencesRow).where(
                PlatformUserPreferencesRow.user_id.in_([clinician, stand_in])
            )
        )


def _connect_while_locked(
    sessions: sessionmaker[Session],
    clinician: str,
    stand_in: str,
    savepoint: Callable[[Session], Callable[[], contextlib.AbstractContextManager[object]]],
) -> bool:
    """Run the callback's write sequence while another connection holds the row.

    Returns whether the earlier write in the same transaction survived the commit.
    """
    registry = MeetingProviderRegistry([GoogleMeetProvider(lambda _user_id: True)])
    with sessions() as locker, sessions() as request:
        locker.execute(
            select(PlatformUserPreferencesRow)
            .where(PlatformUserPreferencesRow.user_id == clinician)
            .with_for_update()
        ).one()

        request.execute(text("SET LOCAL lock_timeout = '200ms'"))
        repo = PostgresUserRepository(request)
        repo.update_preferences(stand_in, {"default_duration_minutes": 45})
        request.flush()
        _default_to_meet(repo, clinician, registry=registry, savepoint=savepoint(request))
        request.commit()
        locker.rollback()

    with sessions() as check:
        return check.get(PlatformUserPreferencesRow, stand_in) is not None


def test_the_earlier_write_survives_a_default_that_times_out(
    sessions: sessionmaker[Session], users: tuple[str, str]
) -> None:
    clinician, stand_in = users

    survived = _connect_while_locked(
        sessions, clinician, stand_in, lambda session: session.begin_nested
    )

    assert survived is True
    with sessions() as check:
        row = check.get(PlatformUserPreferencesRow, clinician)
        assert row is not None
        assert "default_video_platform" not in row.preferences


def test_without_the_savepoint_the_earlier_write_is_lost(
    sessions: sessionmaker[Session], users: tuple[str, str]
) -> None:
    """The control: proves the timeout really aborts the transaction."""
    clinician, stand_in = users

    survived = _connect_while_locked(
        sessions, clinician, stand_in, lambda _session: contextlib.nullcontext
    )

    assert survived is False
