# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Real-Postgres proof for the telehealth columns and the connection table.

Three things no unit test can reach:

* A **freshly provisioned** practice schema has the columns and the table.
  Provisioning applies the captured template rather than running the chain,
  so a revision that landed without a regenerated template ships a schema
  that exists for every migrated practice and is missing from every new one.
* The vendor's handle **round-trips** through the indexed lookup the webhook
  uses to find an appointment it knows nothing else about.
* A clinician's stored grant is **encrypted at rest**: the column holds
  ciphertext, and the plaintext token never appears in it.

Run: ``make test-integration``.
"""

from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from alembic import command
from alembic.config import Config
from app.db import _current_tenant_schema, arm_current_user_id, set_tenant_schema
from app.meeting_providers.pid import room_handle
from app.meeting_providers.zoom_client import ZoomGrant
from app.models import Patient
from app.repositories.postgres.appointment import PostgresAppointmentRepository
from app.repositories.postgres.patient import PostgresPatientRepository
from app.repositories.postgres.telehealth_connection import PostgresZoomConnectionStore
from app.scheduling_engine.models.appointment import Appointment
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

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

_NOW = datetime(2026, 6, 1, 10, 0, tzinfo=UTC)
_ACCESS_TOKEN = "zoom-access-token-that-must-not-be-readable"  # noqa: S105 — a fixture value
_REFRESH_TOKEN = "zoom-refresh-token-that-must-not-be-readable"  # noqa: S105 — a fixture value


@pytest.fixture(autouse=True)
def _encryption_key(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """The grant is encrypted at rest under the deployment's own key.

    A placeholder that happens to be 32 valid base64 bytes, as the compose
    stack's is: it only ever encrypts this test's throwaway schema.
    """
    from app.settings import get_settings  # noqa: PLC0415

    monkeypatch.setenv("GOOGLE_CALENDAR_ENCRYPTION_KEY", "A" * 43 + "=")
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
    """A practice provisioned the way a real one is: from the template."""
    from app.db.provisioning import create_practice_schema  # noqa: PLC0415

    schema = f"practice_test_telehealth_{uuid.uuid4().hex[:8]}"
    create_practice_schema(engine, schema)
    yield schema
    with engine.connect() as conn:
        conn.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        conn.commit()


@pytest.fixture
def session(engine: Engine, tenant_schema: str) -> Iterator[Session]:
    sess = Session(bind=engine)
    set_tenant_schema(sess, tenant_schema)
    try:
        yield sess
    finally:
        sess.rollback()
        sess.close()
        _current_tenant_schema.set(None)


def _columns(session: Session, schema: str, table: str) -> set[str]:
    rows = session.execute(
        text(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema = :schema AND table_name = :table"
        ),
        {"schema": schema, "table": table},
    ).fetchall()
    return {row[0] for row in rows}


class TestAFreshlyProvisionedPractice:
    def test_has_the_telehealth_columns_on_appointments(
        self, session: Session, tenant_schema: str
    ) -> None:
        present = _columns(session, tenant_schema, "appointments")
        assert {
            "provider",
            "meeting_external_id",
            "telehealth_checked_in_at",
            "telehealth_started_at",
            "telehealth_ended_at",
        } <= present

    def test_has_the_connection_table(self, session: Session, tenant_schema: str) -> None:
        assert _columns(session, tenant_schema, "telehealth_connections") == {
            "user_id",
            "provider",
            "encrypted_tokens",
            "account_handle",
            "connected_at",
            "last_error",
        }

    def test_the_handle_column_is_indexed(self, session: Session, tenant_schema: str) -> None:
        """The webhook finds an appointment by handle and nothing else."""
        indexes = session.execute(
            text(
                "SELECT indexname FROM pg_indexes "
                "WHERE schemaname = :schema AND tablename = 'appointments'"
            ),
            {"schema": tenant_schema},
        ).fetchall()
        assert "ix_appointments_meeting_external_id" in {row[0] for row in indexes}


class TestTheHandleRoundTrips:
    def test_an_appointment_is_found_by_the_handle_in_its_room_url(self, session: Session) -> None:
        user_id = str(uuid.uuid4())
        session.commit()
        arm_current_user_id(session, user_id)

        patient_id = str(uuid.uuid4())
        PostgresPatientRepository(session).create(
            Patient(
                id=patient_id,
                first_name="Pat",
                last_name="Ient",
                created_at=_NOW,
                updated_at=_NOW,
            ),
            user_id,
        )

        appointment_id = str(uuid.uuid4())
        handle = room_handle(appointment_id)
        PostgresAppointmentRepository(session).create(
            Appointment(
                id=appointment_id,
                user_id=user_id,
                patient_id=patient_id,
                title="Session",
                start_at=_NOW,
                end_at=_NOW + timedelta(minutes=50),
                duration_minutes=50,
                status="confirmed",
                session_type="individual",
                provider="doxy_me",
                meeting_external_id=handle,
                created_at=_NOW,
                updated_at=_NOW,
            )
        )
        session.commit()

        found = session.execute(
            text("SELECT id FROM appointments WHERE meeting_external_id = :handle"),
            {"handle": handle},
        ).scalar_one()
        assert str(found) == appointment_id

        # And the handle is not the id it was derived from, which is the whole
        # reason the room URL carries one rather than the other.
        assert handle != appointment_id


class TestTheStoredGrant:
    def test_the_token_never_reaches_the_column_in_the_clear(self, session: Session) -> None:
        user_id = str(uuid.uuid4())
        session.commit()
        arm_current_user_id(session, user_id)

        store = PostgresZoomConnectionStore(session)
        store.save(
            user_id,
            ZoomGrant(
                access_token=_ACCESS_TOKEN,
                refresh_token=_REFRESH_TOKEN,
                expires_at=_NOW + timedelta(hours=1),
                account_handle="practice@example.test",
            ),
        )
        session.commit()

        stored = session.execute(
            text(
                "SELECT encrypted_tokens, account_handle FROM telehealth_connections "
                "WHERE user_id = :user_id AND provider = 'zoom'"
            ),
            {"user_id": user_id},
        ).one()

        assert _ACCESS_TOKEN not in stored[0]
        assert _REFRESH_TOKEN not in stored[0]
        # The account label stays readable so a settings page can name it.
        assert stored[1] == "practice@example.test"

    def test_it_reads_back_as_the_grant_that_went_in(self, session: Session) -> None:
        user_id = str(uuid.uuid4())
        session.commit()
        arm_current_user_id(session, user_id)

        store = PostgresZoomConnectionStore(session)
        expires_at = _NOW + timedelta(hours=1)
        store.save(
            user_id,
            ZoomGrant(
                access_token=_ACCESS_TOKEN,
                refresh_token=_REFRESH_TOKEN,
                expires_at=expires_at,
                account_handle="practice@example.test",
            ),
        )
        session.commit()

        grant = store.get(user_id)

        assert grant is not None
        assert grant.access_token == _ACCESS_TOKEN
        assert grant.refresh_token == _REFRESH_TOKEN
        assert grant.expires_at == expires_at

    def test_disconnecting_removes_it(self, session: Session) -> None:
        user_id = str(uuid.uuid4())
        session.commit()
        arm_current_user_id(session, user_id)

        store = PostgresZoomConnectionStore(session)
        store.save(
            user_id,
            ZoomGrant(
                access_token=_ACCESS_TOKEN,
                refresh_token=_REFRESH_TOKEN,
                expires_at=_NOW + timedelta(hours=1),
            ),
        )
        session.commit()

        assert store.delete(user_id) is True
        assert store.get(user_id) is None
        assert store.delete(user_id) is False
