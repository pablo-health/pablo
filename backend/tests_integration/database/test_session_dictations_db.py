# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Session dictations against real Postgres.

The unit tests prove the rules over the in-memory repository. This proves
the Postgres repository round-trips a dictation, records its transcript and
the addendum it became, and that the table refuses a status or use it doesn't
know.

Run: ``make test-integration``.
"""

from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

if TYPE_CHECKING:
    from collections.abc import Iterator

    from sqlalchemy.engine import Engine

_DB_URL = os.environ.get("DATABASE_URL", "")
pytestmark = pytest.mark.skipif(
    not _DB_URL or os.environ.get("DATABASE_BACKEND") != "postgres",
    reason=(
        "PostgreSQL not configured. Set DATABASE_URL and DATABASE_BACKEND=postgres "
        "or run via make test-integration."
    ),
)

_CLINICIAN = "7a1d3e95-4c2b-5f60-9e18-3b6d8a2c4f71"


@pytest.fixture(scope="module")
def engine() -> Iterator[Engine]:
    backend_dir = Path(__file__).resolve().parents[2]
    cfg = Config(str(backend_dir / "alembic.ini"))
    cfg.set_main_option("script_location", str(backend_dir / "alembic"))
    command.upgrade(cfg, "head")
    eng = create_engine(_DB_URL, pool_pre_ping=True)
    yield eng
    eng.dispose()


@pytest.fixture(scope="module")
def tenant_schema(engine: Engine) -> Iterator[str]:
    from app.db.provisioning import create_practice_schema  # noqa: PLC0415

    with engine.connect() as conn:
        conn.execute(text("SET search_path = practice, platform, public"))
        conn.commit()

    schema = f"practice_test_dictations_{uuid.uuid4().hex[:8]}"
    create_practice_schema(engine, schema)
    yield schema
    with engine.connect() as conn:
        conn.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        conn.commit()


def _session(engine: Engine, schema: str) -> Session:
    session = Session(engine)
    session.execute(text(f"SET search_path = {schema}, platform, public"))
    session.execute(text("SELECT set_config('app.current_user_id', :u, false)"), {"u": _CLINICIAN})
    return session


@pytest.fixture(scope="module")
def session_and_note(engine: Engine, tenant_schema: str) -> tuple[str, str, str]:
    """A patient the clinician can see, a session, and its drafted note."""
    patient_id, session_id, note_id = (str(uuid.uuid4()) for _ in range(3))
    with _session(engine, tenant_schema) as db:
        db.execute(
            text(
                "INSERT INTO patients (id, first_name, last_name, "
                "first_name_lower, last_name_lower, status, "
                "session_count, created_at, updated_at) "
                "VALUES (CAST(:pid AS uuid), 'Ada', 'Lovelace', "
                "'ada', 'lovelace', 'active', 0, now(), now())"
            ),
            {"pid": patient_id},
        )
        db.execute(
            text(
                "INSERT INTO patient_clinicians (patient_id, user_id, granted_by) "
                "VALUES (CAST(:pid AS uuid), :u, :u)"
            ),
            {"pid": patient_id, "u": _CLINICIAN},
        )
        db.execute(
            text(
                "INSERT INTO therapy_sessions (id, user_id, patient_id, session_date, "
                "session_number, status, transcript, created_at) "
                "VALUES (CAST(:id AS uuid), CAST(:u AS uuid), CAST(:pid AS uuid), now(), 1, "
                "'pending_review', CAST(:t AS jsonb), now())"
            ),
            {"id": session_id, "u": _CLINICIAN, "pid": patient_id, "t": '{"content": "x"}'},
        )
        db.execute(
            text(
                "INSERT INTO notes (id, patient_id, session_id, note_type, content, status, "
                "author_user_id, created_at, updated_at) "
                "VALUES (CAST(:id AS uuid), CAST(:pid AS uuid), CAST(:sid AS uuid), 'soap', "
                "CAST(:c AS jsonb), 'complete', CAST(:u AS uuid), now(), now())"
            ),
            {"id": note_id, "pid": patient_id, "sid": session_id, "c": "{}", "u": _CLINICIAN},
        )
        db.commit()
    return patient_id, session_id, note_id


def test_a_dictation_round_trips_and_records_what_it_became(
    engine: Engine, tenant_schema: str, session_and_note: tuple[str, str, str]
) -> None:
    from app.models.session_dictation import SessionDictation  # noqa: PLC0415
    from app.repositories.postgres.session_dictation import (  # noqa: PLC0415
        PostgresSessionDictationRepository,
    )

    patient_id, session_id, note_id = session_and_note
    dictation_id = str(uuid.uuid4())
    with _session(engine, tenant_schema) as db:
        repo = PostgresSessionDictationRepository(db)
        repo.add(
            SessionDictation(
                id=dictation_id,
                session_id=session_id,
                note_id=note_id,
                patient_id=patient_id,
                author_user_id=_CLINICIAN,
                audio_path=f"dictations/{session_id}/{dictation_id}",
                content_type="audio/webm",
                status="transcribing",
                created_at=datetime.now(UTC),
                duration_seconds=42,
            )
        )

        recorded = repo.record_transcript(
            dictation_id,
            status="transcribed",
            transcript="Next session in two weeks.",
            used_as="redraft",
            transcribed_at=datetime.now(UTC),
        )
        assert (recorded.status, recorded.used_as, recorded.duration_seconds) == (
            "transcribed",
            "redraft",
            42,
        )

        listed = repo.list_for_session(session_id)
        assert [d.id for d in listed] == [dictation_id]
        assert listed[0].transcript == "Next session in two weeks."


def test_the_table_refuses_an_unknown_status(
    engine: Engine, tenant_schema: str, session_and_note: tuple[str, str, str]
) -> None:
    patient_id, session_id, note_id = session_and_note
    with _session(engine, tenant_schema) as db, pytest.raises(IntegrityError):
        db.execute(
            text(
                "INSERT INTO session_dictations (id, session_id, note_id, patient_id, "
                "author_user_id, audio_path, content_type, status, created_at) "
                "VALUES (CAST(:id AS uuid), CAST(:sid AS uuid), CAST(:nid AS uuid), "
                "CAST(:pid AS uuid), CAST(:u AS uuid), 'x', 'audio/webm', 'lost', now())"
            ),
            {
                "id": str(uuid.uuid4()),
                "sid": session_id,
                "nid": note_id,
                "pid": patient_id,
                "u": _CLINICIAN,
            },
        )
