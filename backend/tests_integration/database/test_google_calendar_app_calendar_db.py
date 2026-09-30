# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Real-Postgres tests for remembering the calendar Pablo makes.

The unit suite (``tests/test_google_calendar.py``) drives the service with a
``MagicMock`` repository, so it can only assume what a disconnect leaves
behind. These tests run a connect, a disconnect and a second connect through
the real repository against a provisioned tenant schema, with only Google
mocked: the second connect must find the first calendar, and the disconnected
row must not read as a connection anywhere.

Run: ``make test-integration``.
"""

from __future__ import annotations

import base64
import os
import uuid
from pathlib import Path
from typing import TYPE_CHECKING
from unittest.mock import MagicMock, patch

import pytest
from alembic import command
from alembic.config import Config
from app.calendar_providers import pkce_store
from app.db import _current_tenant_schema, arm_current_user_id, set_tenant_schema
from app.repositories.google_calendar_token import GoogleCalendarTokenDoc
from app.repositories.postgres.google_calendar_token import (
    PostgresGoogleCalendarTokenRepository,
)
from app.services.google_calendar_service import GoogleCalendarService
from app.settings import get_settings
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session
from tests.calendar_oauth_fakes import FakePkceRedis, authorized_state

if TYPE_CHECKING:
    from collections.abc import Iterator

    from sqlalchemy.engine import Engine


_db_url = os.environ.get("DATABASE_URL", "")
pytestmark = pytest.mark.skipif(
    not _db_url or os.environ.get("DATABASE_BACKEND") != "postgres",
    reason=(
        "PostgreSQL not configured. Set DATABASE_URL and "
        "DATABASE_BACKEND=postgres; testcontainers should set both."
    ),
)

_FIRST = "first@group.calendar.google.com"
_SECOND = "second@group.calendar.google.com"


@pytest.fixture(scope="module")
def engine() -> Iterator[Engine]:
    backend_dir = Path(__file__).resolve().parents[2]
    cfg = Config(str(backend_dir / "alembic.ini"))
    cfg.set_main_option("script_location", str(backend_dir / "alembic"))
    command.upgrade(cfg, "head")
    eng = create_engine(_db_url, pool_pre_ping=True)
    yield eng
    eng.dispose()


@pytest.fixture(scope="module")
def tenant_schema(engine: Engine) -> Iterator[str]:
    from app.db.provisioning import create_practice_schema  # noqa: PLC0415

    schema = f"practice_test_gcal_{uuid.uuid4().hex[:8]}"
    create_practice_schema(engine, schema)
    yield schema
    with engine.connect() as conn:
        conn.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        conn.commit()


@pytest.fixture(autouse=True)
def _oauth_environment(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    key = base64.b64encode(os.urandom(32)).decode()
    monkeypatch.setenv("GOOGLE_CALENDAR_ENCRYPTION_KEY", key)
    redis = FakePkceRedis()
    monkeypatch.setattr(pkce_store, "get_redis_client", lambda: redis)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def user_id() -> str:
    return str(uuid.uuid4())


@pytest.fixture
def session(engine: Engine, tenant_schema: str, user_id: str) -> Iterator[Session]:
    sess = Session(bind=engine)
    set_tenant_schema(sess, tenant_schema)
    arm_current_user_id(sess, user_id)
    try:
        yield sess
    finally:
        sess.rollback()
        sess.close()
        _current_tenant_schema.set(None)


@pytest.fixture
def repo(session: Session) -> PostgresGoogleCalendarTokenRepository:
    return PostgresGoogleCalendarTokenRepository(session)


@pytest.fixture
def service(repo: PostgresGoogleCalendarTokenRepository) -> GoogleCalendarService:
    return GoogleCalendarService(
        token_repo=repo,
        appointment_repo=MagicMock(),
        client_id="test-client-id",
        client_secret="test-client-secret",  # noqa: S106
    )


def _connect(service: GoogleCalendarService, user_id: str, google: MagicMock) -> None:
    # Only the calendar ids matter here; the grant's contents never do.
    credentials = MagicMock(
        token=None, refresh_token=None, token_uri=None, client_id=None, client_secret=None
    )
    with (
        patch("app.services.google_calendar_service._build_flow") as build_flow,
        patch("app.services.google_calendar_service._build_calendar_service") as build_svc,
    ):
        build_flow.return_value.credentials = credentials
        build_svc.return_value = google
        service.handle_callback(
            user_id, "auth-code", "http://localhost/callback", state=authorized_state(user_id)
        )


def _google_that_creates(calendar_id: str) -> MagicMock:
    google = MagicMock()
    google.calendars().insert().execute.return_value = {"id": calendar_id}
    google.calendars().insert.reset_mock()
    return google


def test_reconnecting_after_a_disconnect_reuses_the_calendar(
    service: GoogleCalendarService,
    repo: PostgresGoogleCalendarTokenRepository,
    user_id: str,
) -> None:
    first = _google_that_creates(_FIRST)
    _connect(service, user_id, first)
    first.calendars().insert.assert_called_once()

    assert service.disconnect(user_id) is True

    second = _google_that_creates(_SECOND)
    _connect(service, user_id, second)

    second.calendars().insert.assert_not_called()
    stored = repo.get(user_id)
    assert stored is not None
    assert stored.calendar_id == _FIRST
    assert stored.app_calendar_id == _FIRST


def test_a_different_account_after_a_disconnect_gets_its_own_calendar(
    service: GoogleCalendarService,
    repo: PostgresGoogleCalendarTokenRepository,
    user_id: str,
) -> None:
    _connect(service, user_id, _google_that_creates(_FIRST))
    service.disconnect(user_id)

    other_account = _google_that_creates(_SECOND)
    other_account.calendars().get().execute.side_effect = Exception("404 Not Found")
    _connect(service, user_id, other_account)

    stored = repo.get(user_id)
    assert stored is not None
    assert stored.calendar_id == _SECOND
    assert stored.app_calendar_id == _SECOND


def test_a_disconnected_row_is_not_a_connection(
    service: GoogleCalendarService,
    repo: PostgresGoogleCalendarTokenRepository,
    user_id: str,
) -> None:
    """What survives a disconnect is the calendar's id, and nothing else."""
    _connect(service, user_id, _google_that_creates(_FIRST))
    service.disconnect(user_id)

    assert repo.get(user_id) is None
    assert repo.exists(user_id) is False
    assert user_id not in {doc.user_id for doc in repo.list_all()}
    assert service.get_sync_status(user_id)["connected"] is False
    assert service.disconnect(user_id) is False
    assert repo.get_app_calendar_id(user_id) == _FIRST


def test_disconnecting_a_main_calendar_connection_leaves_nothing(
    repo: PostgresGoogleCalendarTokenRepository,
    session: Session,
    user_id: str,
) -> None:
    """With no calendar of Pablo's to remember, the row goes entirely."""

    repo.save(
        GoogleCalendarTokenDoc(
            user_id=user_id,
            encrypted_tokens="sealed",
            write_target="primary",
            calendar_id="therapist@gmail.com",
        )
    )
    assert repo.delete(user_id) is True

    remaining = session.execute(
        text("SELECT count(*) FROM google_calendar_tokens WHERE user_id = :u"), {"u": user_id}
    ).scalar_one()
    assert remaining == 0
