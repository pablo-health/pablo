# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Disconnecting Google Calendar over the API, against a real tenant schema.

The repository-level test (``test_google_calendar_disconnect_db.py``) shows
which rows go. This drives ``DELETE /api/google-calendar/disconnect`` through
the real middleware, so it can also show what only a request can:

* the grant is revoked with the refresh token, and Google failing to answer
  does not stop the disconnect;
* the tokens and what was read from the calendar go in one transaction — a
  failure after both deletes leaves every row where it was;
* the audit row holds counts and nothing else;
* withdrawing access needs no BAA on file;
* a clinician who is not connected gets a 404 and nothing is revoked or
  recorded.

Google itself is never reached: ``httpx.post`` is replaced per test.

Run: ``make test-integration``.
"""

from __future__ import annotations

import base64
import os
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any
from unittest.mock import MagicMock, patch

import httpx
import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text

if TYPE_CHECKING:
    from collections.abc import Iterator

    from app.models import User
    from fastapi import FastAPI, Request
    from fastapi.testclient import TestClient
    from sqlalchemy.engine import Engine


_db_url = os.environ.get("DATABASE_URL", "")
pytestmark = pytest.mark.skipif(
    not _db_url or os.environ.get("DATABASE_BACKEND") != "postgres",
    reason=(
        "PostgreSQL not configured. Set DATABASE_URL and "
        "DATABASE_BACKEND=postgres; testcontainers should set both."
    ),
)

os.environ.setdefault("ENVIRONMENT", "development")

_TENANT_EMAIL = "e2e-gcal-disconnect@example.com"
_USER_ID = "5d0c3a7e-1f4b-4c8e-9a2d-6b7e8f9a0b1c"
_GRANT_REFRESH = "1//refresh-to-revoke"
_GRANT_ACCESS = "ya29.access-token"
_PABLO_CALENDAR = "made-by-pablo@group.calendar.google.com"
_GOOGLE = "google_calendar"
_FEED = "ical:sessions_health"
_DISCONNECT = "/api/google-calendar/disconnect"
_TABLES = (
    "google_calendar_tokens",
    "google_calendar_settings",
    "external_calendar_events",
    "patient_source_mappings",
    "appointments",
)


@pytest.fixture(scope="module", autouse=True)
def _arming_here_stays_here() -> Iterator[None]:
    """Hand back the request ContextVars as this module found them."""
    from app.db import (  # noqa: PLC0415
        _current_patient_id,
        _current_tenant_schema,
        _current_user_id,
    )

    tokens = [
        (var, var.set(var.get()))
        for var in (_current_user_id, _current_patient_id, _current_tenant_schema)
    ]
    yield
    for var, token in reversed(tokens):
        var.reset(token)


@pytest.fixture(autouse=True)
def _encryption_key(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    from app.settings import get_settings  # noqa: PLC0415

    monkeypatch.setenv("GOOGLE_CALENDAR_ENCRYPTION_KEY", base64.b64encode(os.urandom(32)).decode())
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


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
    from tests_integration.database.test_tenant_resolution_db import (  # noqa: PLC0415
        _seeded_practice,
    )

    # See test_chat_real_db.py: provisioning resolves ``has_patient_access``
    # through the connection's search_path.
    with engine.connect() as conn:
        conn.execute(text("SET search_path = practice, platform, public"))
        conn.commit()

    with _seeded_practice(engine, _TENANT_EMAIL) as schema:
        yield schema
    with engine.connect() as conn:
        conn.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        conn.commit()


@pytest.fixture(autouse=True)
def _empty_tables(engine: Engine, tenant_schema: str) -> None:
    with engine.connect() as conn:
        conn.execute(text(f"SET search_path = {tenant_schema}, platform, public"))
        conn.execute(text("SET LOCAL app.allow_audit_purge = 'on'"))
        conn.execute(
            text(f"TRUNCATE TABLE {', '.join(_TABLES)}, audit_logs RESTART IDENTITY CASCADE")
        )
        conn.commit()


def _user(*, baa_accepted: bool = True) -> User:
    from app.models import User  # noqa: PLC0415

    accepted = datetime(2024, 1, 1, tzinfo=UTC) if baa_accepted else None
    return User(
        id=_USER_ID,
        email=_TENANT_EMAIL,
        name="Disconnecting Clinician",
        created_at=datetime(2024, 1, 1, tzinfo=UTC),
        baa_accepted_at=accepted,
        baa_version="2024-01-01" if baa_accepted else None,
    )


def _client(
    app: FastAPI, tenant_schema: str, user: User, monkeypatch: pytest.MonkeyPatch
) -> TestClient:
    """A client signed in as ``user``. ``require_baa_acceptance`` is left real."""
    from app.auth.providers import VerifiedIdentity  # noqa: PLC0415
    from app.auth.service import (  # noqa: PLC0415
        TenantContext,
        get_current_user,
        get_current_user_id,
        get_current_user_no_mfa,
        get_tenant_context,
    )
    from app.db import arm_current_user_id, get_db_session  # noqa: PLC0415
    from fastapi.testclient import TestClient  # noqa: PLC0415

    identity = VerifiedIdentity(
        provider="test", subject_id=user.id, email=_TENANT_EMAIL, mfa_satisfied=True, claims={}
    )

    def _stash_identity(request: Request) -> None:
        request.state.verified_identity = identity

    monkeypatch.setattr("app.db.middleware._verify_and_stash_clinician_identity", _stash_identity)

    def _tenant_context() -> TenantContext:
        arm_current_user_id(get_db_session(), user.id)
        return TenantContext(
            user_id=user.id, practice_id="test-tenant", practice_schema=tenant_schema
        )

    app.dependency_overrides[get_current_user_id] = lambda: user.id
    app.dependency_overrides[get_current_user] = lambda: user
    app.dependency_overrides[get_current_user_no_mfa] = lambda: user
    app.dependency_overrides[get_tenant_context] = _tenant_context
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture
def app() -> Iterator[FastAPI]:
    from app.main import app as fastapi_app  # noqa: PLC0415

    yield fastapi_app
    fastapi_app.dependency_overrides.clear()


class _Seeded:
    """What a connected clinician's tenant holds before they disconnect."""

    def __init__(self, engine: Engine, schema: str, *, connected: bool = True) -> None:
        from app.db import arm_current_user_id, set_tenant_schema  # noqa: PLC0415
        from app.models.patient import Patient  # noqa: PLC0415
        from app.patients.identifiers import calendar_scope, clinician_scope  # noqa: PLC0415
        from app.repositories.external_calendar_event import (  # noqa: PLC0415
            ExternalCalendarEvent,
        )
        from app.repositories.google_calendar_token import (  # noqa: PLC0415
            GoogleCalendarTokenDoc,
        )
        from app.repositories.patient_source_mapping import (  # noqa: PLC0415
            PatientSourceMapping,
        )
        from app.repositories.postgres.appointment import (  # noqa: PLC0415
            PostgresAppointmentRepository,
        )
        from app.repositories.postgres.external_calendar_event import (  # noqa: PLC0415
            PostgresExternalCalendarEventRepository,
        )
        from app.repositories.postgres.google_calendar_token import (  # noqa: PLC0415
            PostgresGoogleCalendarTokenRepository,
        )
        from app.repositories.postgres.patient import PostgresPatientRepository  # noqa: PLC0415
        from app.repositories.postgres.patient_source_mapping import (  # noqa: PLC0415
            PostgresPatientSourceMappingRepository,
        )
        from app.scheduling_engine.models.appointment import Appointment  # noqa: PLC0415
        from app.services.token_encryption import encrypt_tokens  # noqa: PLC0415
        from sqlalchemy.orm import Session  # noqa: PLC0415

        self.engine, self.schema, self.connected = engine, schema, connected
        now = datetime.now(UTC)
        start = datetime(2099, 1, 5, 15, tzinfo=UTC)
        with Session(bind=engine) as sess:
            set_tenant_schema(sess, schema)
            arm_current_user_id(sess, _USER_ID)
            tokens = PostgresGoogleCalendarTokenRepository(sess)
            if connected:
                tokens.save(
                    GoogleCalendarTokenDoc(
                        user_id=_USER_ID,
                        encrypted_tokens=encrypt_tokens(
                            {"token": _GRANT_ACCESS, "refresh_token": _GRANT_REFRESH}
                        ),
                        write_target="app_calendar",
                        calendar_id=_PABLO_CALENDAR,
                    )
                )
            tokens.remember_app_calendar_id(_USER_ID, _PABLO_CALENDAR)
            tokens.set_followed_calendar(_USER_ID, "primary", main_calendar=True)
            patient = PostgresPatientRepository(sess).create(
                Patient(
                    id=str(uuid.uuid4()),
                    first_name="Followed",
                    last_name="Client",
                    created_at=now,
                    updated_at=now,
                ),
                _USER_ID,
            )
            events = PostgresExternalCalendarEventRepository(sess)
            for source in (_GOOGLE, _GOOGLE, _FEED):
                events.save(
                    ExternalCalendarEvent(
                        id=str(uuid.uuid4()),
                        user_id=_USER_ID,
                        source=source,
                        source_event_id=f"evt-{uuid.uuid4().hex[:8]}",
                        start_at=start,
                        end_at=start + timedelta(minutes=50),
                        title="Followed Client",
                    )
                )
            # An answer about the clinician's own calendar, one about their
            # feed, and one about Google from before answers had a scope.
            mappings = PostgresPatientSourceMappingRepository(sess)
            for source, scope in (
                (_GOOGLE, calendar_scope(_TENANT_EMAIL)),
                (_FEED, clinician_scope(_USER_ID)),
            ):
                mappings.save(
                    PatientSourceMapping(
                        scope=scope,
                        source=source,
                        identifier_digest=f"series:{uuid.uuid4().hex}",
                        patient_id=patient.id,
                        answered_by_user_id=_USER_ID,
                    )
                )
            legacy = f"series:{uuid.uuid4().hex[:8]}"
            sess.execute(
                text(
                    "INSERT INTO patient_source_mappings"
                    " (doc_id, user_id, source, source_identifier, answer, patient_id, created_at)"
                    " VALUES (:d, CAST(:u AS uuid), :s, :i, 'client', CAST(:p AS uuid), now())"
                ),
                {
                    "d": f"{_USER_ID}_{_GOOGLE}_{legacy}",
                    "u": _USER_ID,
                    "s": _GOOGLE,
                    "i": legacy,
                    "p": patient.id,
                },
            )
            self.appointment_id = str(uuid.uuid4())
            PostgresAppointmentRepository(sess).create(
                Appointment(
                    id=self.appointment_id,
                    user_id=_USER_ID,
                    patient_id=patient.id,
                    title="Session",
                    start_at=start,
                    end_at=start + timedelta(minutes=50),
                    duration_minutes=50,
                    status="confirmed",
                    session_type="individual",
                    created_at=now,
                    updated_at=now,
                    google_event_id="pushed-evt",
                    google_calendar_id=_PABLO_CALENDAR,
                    google_sync_status="synced",
                )
            )
            sess.commit()

    def _read(self, statement: Any, params: dict[str, Any] | None = None) -> Any:
        """Read back as the clinician.

        Every table here is row-scoped and the test role does not bypass RLS,
        so an unarmed read sees nothing at all, and "nothing left" would pass
        whether or not anything was deleted.
        """
        from app.db import arm_current_user_id, set_tenant_schema  # noqa: PLC0415
        from sqlalchemy.orm import Session  # noqa: PLC0415

        with Session(bind=self.engine) as sess:
            set_tenant_schema(sess, self.schema)
            arm_current_user_id(sess, _USER_ID)
            return sess.execute(statement, params or {}).all()

    def scalar(self, sql: str, **params: Any) -> Any:
        [row] = self._read(text(sql), params)
        return row[0]

    def rows(self, table_name: str, source: str | None = None) -> int:
        from sqlalchemy import column, func, select, table  # noqa: PLC0415

        rows = table(table_name, column("source"))
        query = select(func.count()).select_from(rows)
        if source is not None:
            query = query.where(rows.c.source == source)
        [(count,)] = self._read(query)
        return int(count)

    def disconnect_audits(self) -> list[Any]:
        return list(
            self._read(
                text(
                    "SELECT resource_id, changes, patient_id FROM audit_logs"
                    " WHERE action = 'google_calendar_disconnected'"
                )
            )
        )

    def assert_seeded(self) -> None:
        """The reads can see what was written — so a zero after is a deletion."""
        assert self.rows("google_calendar_tokens") == (1 if self.connected else 0)
        assert self.rows("external_calendar_events", _GOOGLE) == 2
        assert self.rows("patient_source_mappings", _GOOGLE) == 2


def _revoked(status_code: int = 200) -> MagicMock:
    return MagicMock(return_value=MagicMock(status_code=status_code))


def test_disconnect_revokes_removes_what_was_read_and_keeps_pablos_records(
    app: FastAPI, engine: Engine, tenant_schema: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    seeded = _Seeded(engine, tenant_schema)
    seeded.assert_seeded()
    client = _client(app, tenant_schema, _user(), monkeypatch)

    with patch("httpx.post", _revoked()) as post:
        response = client.delete(_DISCONNECT)

    assert response.status_code == 200, response.text
    post.assert_called_once()
    assert post.call_args.args[0] == "https://oauth2.googleapis.com/revoke"
    assert post.call_args.kwargs["data"] == {"token": _GRANT_REFRESH}

    assert seeded.rows("google_calendar_tokens") == 0
    assert seeded.rows("external_calendar_events", _GOOGLE) == 0
    assert seeded.rows("patient_source_mappings", _GOOGLE) == 0
    assert seeded.rows("external_calendar_events", _FEED) == 1
    assert seeded.rows("patient_source_mappings", _FEED) == 1
    # The session Pablo pushed stays, still naming the event Pablo wrote.
    assert (
        seeded.scalar(
            "SELECT google_event_id FROM appointments WHERE id = CAST(:a AS uuid)",
            a=seeded.appointment_id,
        )
        == "pushed-evt"
    )
    # The calendar Pablo made is remembered for the next connect; following
    # the clinician's calendar is not.
    assert seeded.scalar("SELECT app_calendar_id FROM google_calendar_settings") == (
        _PABLO_CALENDAR
    )
    assert seeded.scalar("SELECT follow_calendar_id FROM google_calendar_settings") is None

    [audit] = seeded.disconnect_audits()
    assert audit.changes == {
        "calendar_events_deleted": 2,
        "remembered_answers_deleted": 2,
    }
    assert audit.patient_id is None
    assert audit.resource_id == "google-calendar"


def test_google_failing_to_answer_still_disconnects(
    app: FastAPI, engine: Engine, tenant_schema: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    seeded = _Seeded(engine, tenant_schema)
    seeded.assert_seeded()
    client = _client(app, tenant_schema, _user(), monkeypatch)

    with patch("httpx.post", side_effect=httpx.ConnectError("unreachable")):
        response = client.delete(_DISCONNECT)

    assert response.status_code == 200, response.text
    assert seeded.rows("google_calendar_tokens") == 0
    assert seeded.rows("external_calendar_events", _GOOGLE) == 0
    assert seeded.rows("patient_source_mappings", _GOOGLE) == 0
    assert len(seeded.disconnect_audits()) == 1


def test_a_failure_after_the_deletes_leaves_every_row_in_place(
    app: FastAPI, engine: Engine, tenant_schema: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Tokens and data go together or not at all.

    The failure is raised after both deletes have been flushed, so only the
    request's single transaction rolling back can put them back. The revoke
    at Google is not undone — it cannot be — which leaves a connection whose
    grant is gone; the next sync reports it and a retried disconnect
    finishes the job.
    """
    from app.calendar_providers import disconnect  # noqa: PLC0415

    seeded = _Seeded(engine, tenant_schema)
    seeded.assert_seeded()
    client = _client(app, tenant_schema, _user(), monkeypatch)
    real = disconnect.forget_google_calendar

    def forget_then_fail(*args: Any, **kwargs: Any) -> Any:
        real(*args, **kwargs)
        raise RuntimeError("after the deletes")

    monkeypatch.setattr("app.routes.scheduling.forget_google_calendar", forget_then_fail)
    with patch("httpx.post", _revoked()):
        response = client.delete(_DISCONNECT)

    assert response.status_code == 500
    assert seeded.rows("google_calendar_tokens") == 1
    assert seeded.rows("external_calendar_events", _GOOGLE) == 2
    assert seeded.rows("patient_source_mappings", _GOOGLE) == 2
    assert seeded.scalar(
        "SELECT google_event_id FROM appointments WHERE id = CAST(:a AS uuid)",
        a=seeded.appointment_id,
    ) == ("pushed-evt")
    assert seeded.disconnect_audits() == []
    assert seeded.scalar("SELECT follow_calendar_id FROM google_calendar_settings") == "primary"


def test_withdrawing_access_needs_no_baa_on_file(
    app: FastAPI, engine: Engine, tenant_schema: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    seeded = _Seeded(engine, tenant_schema)
    seeded.assert_seeded()
    client = _client(app, tenant_schema, _user(baa_accepted=False), monkeypatch)

    with patch("httpx.post", _revoked()):
        response = client.delete(_DISCONNECT)

    assert response.status_code == 200, response.text
    assert seeded.rows("google_calendar_tokens") == 0


def test_not_connected_is_a_404_that_revokes_and_records_nothing(
    app: FastAPI, engine: Engine, tenant_schema: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    seeded = _Seeded(engine, tenant_schema, connected=False)
    seeded.assert_seeded()
    client = _client(app, tenant_schema, _user(), monkeypatch)

    with patch("httpx.post", _revoked()) as post:
        response = client.delete(_DISCONNECT)

    assert response.status_code == 404
    post.assert_not_called()
    assert seeded.disconnect_audits() == []


def test_a_second_disconnect_is_a_404(
    app: FastAPI, engine: Engine, tenant_schema: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    _Seeded(engine, tenant_schema)
    client = _client(app, tenant_schema, _user(), monkeypatch)

    with patch("httpx.post", _revoked()) as post:
        first = client.delete(_DISCONNECT)
        second = client.delete(_DISCONNECT)

    assert (first.status_code, second.status_code) == (200, 404)
    post.assert_called_once()
