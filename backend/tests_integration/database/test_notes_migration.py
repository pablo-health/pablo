# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Migration tests for the notes/sessions split (pa-0nx.1 + pa-0nx.2).

What these tests prove that unit tests can't:
  - the notes table + indexes exist after upgrade-to-head
  - the partial unique index on ``session_id`` permits multiple NULL
    standalone notes
  - pa-0nx.2's migration ``c8a9d3e4f206`` drops the legacy note columns
    from ``therapy_sessions`` while preserving rows in ``notes``

Skipped automatically when no Postgres is configured — see
``DATABASE_URL`` / ``DATABASE_BACKEND`` env. Run via:
``make test-integration``.
"""

from __future__ import annotations

import json
import os
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

if TYPE_CHECKING:
    from collections.abc import Iterator

    from sqlalchemy.engine import Engine
    from sqlalchemy.orm import Session

_db_url = os.environ.get("DATABASE_URL", "")
pytestmark = pytest.mark.skipif(
    not _db_url or os.environ.get("DATABASE_BACKEND") != "postgres",
    reason=(
        "PostgreSQL not configured. Set DATABASE_URL and DATABASE_BACKEND=postgres; "
        "apply migrations with `make db-migrate`."
    ),
)


@pytest.fixture(scope="module")
def engine() -> Iterator[Engine]:
    backend_dir = Path(__file__).resolve().parents[2]
    cfg = Config(str(backend_dir / "alembic.ini"))
    cfg.set_main_option("script_location", str(backend_dir / "alembic"))
    command.upgrade(cfg, "head")

    eng = create_engine(_db_url, pool_pre_ping=True)
    yield eng
    eng.dispose()


@pytest.fixture
def pg_session(engine: Engine) -> Iterator[Session]:
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    session = factory()
    session.execute(text("SET search_path = practice, platform, public"))
    session.execute(text("TRUNCATE TABLE practice.notes"))
    session.execute(text("TRUNCATE TABLE practice.therapy_sessions"))
    session.commit()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


def _insert_session_recording_only(
    session: Session,
    *,
    session_id: str,
    patient_id: str = "patient-mig",
) -> None:
    """Insert a recording-only therapy_sessions row (post-pa-0nx.2 schema).

    The legacy note columns (note_content, finalized_at, quality_rating,
    export_*, etc.) were dropped in c8a9d3e4f206; note state lives on
    ``notes`` now.
    """
    now = datetime.now(UTC)
    session.execute(
        text(
            """
            INSERT INTO practice.therapy_sessions (
                id, user_id, patient_id, session_date, session_number,
                status, transcript, created_at
            )
            VALUES (
                :id, :uid, :pid, :sd, 1, 'finalized',
                CAST(:tr AS jsonb), :now
            )
            """
        ),
        {
            "id": session_id,
            "uid": "user-mig",
            "pid": patient_id,
            "sd": now,
            "tr": '{"format": "text", "content": "x"}',
            "now": now,
        },
    )


def _insert_note(
    session: Session,
    *,
    note_id: str,
    session_id: str | None,
    patient_id: str = "patient-mig",
    content: dict | None = None,
) -> None:
    now = datetime.now(UTC)
    session.execute(
        text(
            """
            INSERT INTO practice.notes (
                id, patient_id, session_id, note_type, content,
                created_at, updated_at, export_status
            )
            VALUES (
                :id, :pid, :sid, 'soap', CAST(:c AS jsonb),
                :now, :now, 'not_queued'
            )
            """
        ),
        {
            "id": note_id,
            "pid": patient_id,
            "sid": session_id,
            "c": json.dumps(content) if content is not None else None,
            "now": now,
        },
    )


def test_notes_table_and_indexes_exist(pg_session: Session) -> None:
    cols = {
        row[0]
        for row in pg_session.execute(
            text(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema = 'practice' AND table_name = 'notes'"
            )
        ).all()
    }
    expected = {
        "id",
        "patient_id",
        "session_id",
        "note_type",
        "content",
        "content_edited",
        "finalized_at",
        "quality_rating",
        "quality_rating_reason",
        "quality_rating_sections",
        "export_status",
        "export_queued_at",
        "export_reviewed_at",
        "export_reviewed_by",
        "exported_at",
        "redacted_content",
        "naturalized_content",
        "redacted_export_payload",
        "created_at",
        "updated_at",
    }
    assert expected.issubset(cols)

    indexes = {
        row[0]
        for row in pg_session.execute(
            text(
                "SELECT indexname FROM pg_indexes "
                "WHERE schemaname = 'practice' AND tablename = 'notes'"
            )
        ).all()
    }
    assert "ux_notes_session_id" in indexes
    assert "ix_notes_patient_finalized" in indexes


def test_partial_unique_index_allows_multiple_null_session_ids(
    pg_session: Session,
) -> None:
    now = datetime.now(UTC)
    for _ in range(2):
        pg_session.execute(
            text(
                """
                INSERT INTO practice.notes (
                    id, patient_id, session_id, note_type,
                    created_at, updated_at, export_status
                )
                VALUES (:id, :pid, NULL, 'soap', :now, :now, 'not_queued')
                """
            ),
            {"id": str(uuid.uuid4()), "pid": "p-1", "now": now},
        )
    pg_session.commit()


def test_drop_legacy_note_columns_from_therapy_sessions(pg_session: Session) -> None:
    """pa-0nx.2 migration ``c8a9d3e4f206`` drops legacy note columns.

    Asserts that, after upgrade-to-head, ``therapy_sessions`` no longer
    carries any of the soap/quality/export columns — those now live on
    ``notes``.
    """
    cols = {
        row[0]
        for row in pg_session.execute(
            text(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema = 'practice' AND table_name = 'therapy_sessions'"
            )
        ).all()
    }
    legacy = {
        "note_type",
        "note_content",
        "note_content_edited",
        "quality_rating",
        "quality_rating_reason",
        "quality_rating_sections",
        "finalized_at",
        "redacted_soap_note",
        "naturalized_soap_note",
        "export_status",
        "export_queued_at",
        "export_reviewed_at",
        "export_reviewed_by",
        "exported_at",
    }
    assert cols.isdisjoint(legacy), (
        f"therapy_sessions still has legacy columns after pa-0nx.2: "
        f"{sorted(cols & legacy)}"
    )

    # Recording-only columns survive.
    assert {"id", "transcript", "status", "session_date"}.issubset(cols)


def test_drop_migration_preserves_existing_note_rows(pg_session: Session) -> None:
    """Notes seeded before the drop migration survive — only the
    therapy_sessions schema changed, not the notes data."""
    note_id = str(uuid.uuid4())
    sid = str(uuid.uuid4())
    _insert_session_recording_only(pg_session, session_id=sid)
    _insert_note(
        pg_session,
        note_id=note_id,
        session_id=sid,
        content={"subjective": "S"},
    )
    pg_session.commit()

    row = pg_session.execute(
        text("SELECT session_id, content FROM practice.notes WHERE id = :id"),
        {"id": note_id},
    ).one()
    assert row[0] == sid
    assert row[1] == {"subjective": "S"}
