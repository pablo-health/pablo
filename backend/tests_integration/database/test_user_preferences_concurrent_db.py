# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Two saves of different preferences, close together, must both survive.

A full-replace save builds its body from a copy read earlier. When two of
them overlap — two settings changed in quick succession, or two tabs — the
second writes back the first one's key as it was before the first save,
erasing it. ``PostgresUserRepository.update_preferences`` merges inside
Postgres instead, so a writer never starts from a copy.

The failure only exists across connections, so each writer here is a thread
with its own session, and the interleaving is forced rather than hoped for:
both writers get ready (a full replace reads its copy), the first writes and
holds its transaction open, and the second writes while it is held. Run
through the old full-replace path the same interleaving loses a key; that
test is kept to show the overlap really is being forced.
"""

from __future__ import annotations

import os
import threading
import time
import uuid
from typing import TYPE_CHECKING, Any

import pytest
from app.db.platform_models import PlatformUserPreferencesRow
from app.models import UserPreferences
from app.repositories.postgres.user import PostgresUserRepository
from sqlalchemy import create_engine, delete
from sqlalchemy.orm import Session, sessionmaker

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

pytestmark = pytest.mark.skipif(
    os.environ.get("DATABASE_BACKEND") != "postgres",
    reason="Requires the Postgres integration database.",
)

#: How long the first writer holds its transaction open, so the second writer
#: reliably reaches its write while the first is uncommitted.
_HOLD_SECONDS = 0.5


def _merging(repo: PostgresUserRepository, user_id: str) -> Callable[[dict[str, Any]], None]:
    return lambda change: repo.update_preferences(user_id, change)


def _full_replace(repo: PostgresUserRepository, user_id: str) -> Callable[[dict[str, Any]], None]:
    # What a client sending its cached copy does: read now, write copy+change later.
    copy = repo.get_preferences(user_id)
    return lambda change: repo.save_preferences(user_id, copy.model_copy(update=change))


@pytest.fixture(scope="module")
def sessions() -> Iterator[sessionmaker[Session]]:
    engine = create_engine(os.environ["DATABASE_URL"], pool_size=10)
    yield sessionmaker(bind=engine, expire_on_commit=False)
    engine.dispose()


@pytest.fixture
def user_id(sessions: sessionmaker[Session]) -> Iterator[str]:
    uid = str(uuid.uuid4())
    yield uid
    with sessions.begin() as session:
        session.execute(
            delete(PlatformUserPreferencesRow).where(PlatformUserPreferencesRow.user_id == uid)
        )


def _seed(sessions: sessionmaker[Session], user_id: str) -> None:
    with sessions.begin() as session:
        PostgresUserRepository(session).save_preferences(user_id, UserPreferences())


def _saved(sessions: sessionmaker[Session], user_id: str) -> dict[str, Any]:
    with sessions() as session:
        row = session.get(PlatformUserPreferencesRow, user_id)
        assert row is not None
        return dict(row.preferences)


def _run_threads(targets: list[Callable[[], None]]) -> None:
    errors: list[BaseException] = []

    def guarded(target: Callable[[], None]) -> None:
        try:
            target()
        except BaseException as exc:
            errors.append(exc)

    threads = [threading.Thread(target=guarded, args=(target,)) for target in targets]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)
    assert errors == []


def _overlap(
    sessions: sessionmaker[Session],
    user_id: str,
    prepare: Callable[[PostgresUserRepository, str], Callable[[dict[str, Any]], None]],
    first: dict[str, Any],
    second: dict[str, Any],
) -> None:
    """Two writers, the second writing while the first is still uncommitted."""
    both_ready = threading.Barrier(2)
    first_has_written = threading.Event()

    def first_writer() -> None:
        with sessions() as session:
            write = prepare(PostgresUserRepository(session), user_id)
            both_ready.wait()
            write(first)
            session.flush()
            first_has_written.set()
            time.sleep(_HOLD_SECONDS)
            session.commit()

    def second_writer() -> None:
        with sessions() as session:
            write = prepare(PostgresUserRepository(session), user_id)
            both_ready.wait()
            first_has_written.wait()
            write(second)
            session.commit()

    _run_threads([first_writer, second_writer])


class TestOverlappingSaves:
    def test_two_overlapping_patches_to_different_keys_both_persist(
        self, sessions: sessionmaker[Session], user_id: str
    ) -> None:
        _seed(sessions, user_id)

        _overlap(
            sessions, user_id, _merging, {"people_term": "patients"}, {"timezone": "Asia/Tokyo"}
        )

        saved = _saved(sessions, user_id)
        assert saved["people_term"] == "patients"
        assert saved["timezone"] == "Asia/Tokyo"

    def test_the_same_overlap_through_a_full_replace_loses_the_first_key(
        self, sessions: sessionmaker[Session], user_id: str
    ) -> None:
        # Why PATCH exists. If this ever stops losing a key, the overlap above
        # is no longer being forced and that test proves nothing.
        _seed(sessions, user_id)

        _overlap(
            sessions,
            user_id,
            _full_replace,
            {"people_term": "patients"},
            {"timezone": "Asia/Tokyo"},
        )

        saved = _saved(sessions, user_id)
        assert saved["timezone"] == "Asia/Tokyo"
        assert saved["people_term"] is None

    def test_many_simultaneous_first_saves_all_land(
        self, sessions: sessionmaker[Session], user_id: str
    ) -> None:
        # No row yet, so every writer races to INSERT. ON CONFLICT turns the
        # losers into merges rather than unique-key errors or overwrites.
        changes: list[dict[str, Any]] = [
            {"default_video_platform": "google_meet"},
            {"default_session_type": "group"},
            {"quality_preset": "high"},
            {"therapist_display_name": "Dr. Rivera"},
            {"calendar_default_view": "dayGridMonth"},
            {"timezone": "Europe/Berlin"},
            {"theme": "dark"},
            {"calendar_density": "compact"},
        ]
        start = threading.Barrier(len(changes))

        def writer(change: dict[str, Any]) -> Callable[[], None]:
            def write() -> None:
                with sessions() as session:
                    start.wait()
                    PostgresUserRepository(session).update_preferences(user_id, change)
                    session.commit()

            return write

        _run_threads([writer(change) for change in changes])

        saved = _saved(sessions, user_id)
        for change in changes:
            for key, value in change.items():
                assert saved[key] == value


class TestMergeSemantics:
    def test_null_removes_the_key_and_it_reads_back_as_the_default(
        self, sessions: sessionmaker[Session], user_id: str
    ) -> None:
        with sessions.begin() as session:
            repo = PostgresUserRepository(session)
            repo.update_preferences(user_id, {"theme": "dark", "timezone": "Asia/Tokyo"})

        with sessions.begin() as session:
            prefs = PostgresUserRepository(session).update_preferences(user_id, {"theme": None})

        assert "theme" not in _saved(sessions, user_id)
        assert prefs.theme == "warm-paper"
        assert prefs.timezone == "Asia/Tokyo"

    def test_a_read_in_the_same_request_sees_the_merge(
        self, sessions: sessionmaker[Session], user_id: str
    ) -> None:
        # The people-term route writes and then reads back through the same
        # session. A row loaded earlier in that session must not shadow the
        # value Postgres just merged.
        _seed(sessions, user_id)
        with sessions.begin() as session:
            repo = PostgresUserRepository(session)
            assert repo.get_preferences(user_id).people_term is None
            repo.update_preferences(user_id, {"people_term": "patients"})

            assert repo.get_preferences(user_id).people_term == "patients"
