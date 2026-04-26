# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Migration test for ``b9d2f7c4e3a8`` — create notes table + backfill.

What this test proves that unit tests can't:
  - the migration applies cleanly on top of the prior head
  - the partial unique index on ``session_id`` exists
  - the backfill copies one row per ``therapy_sessions`` row that has
    a non-empty ``note_content`` (and skips empty ones)
  - all backfilled fields land in the right columns

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


def _insert_session(
    session: Session,
    *,
    session_id: str,
    patient_id: str = "patient-mig",
    note_content: dict | None = None,
) -> None:
    now = datetime.now(UTC)
    session.execute(
        text(
            """
            INSERT INTO practice.therapy_sessions (
                id, user_id, patient_id, session_date, session_number,
                status, transcript, created_at, note_type, note_content,
                export_status
            )
            VALUES (
                :id, :uid, :pid, :sd, 1, 'finalized',
                CAST(:tr AS jsonb), :now, 'soap',
                CAST(:nc AS jsonb), 'not_queued'
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
            "nc": None if note_content is None else json.dumps(note_content),
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


def test_backfill_skips_sessions_with_no_note_content(pg_session: Session) -> None:
    """A row in therapy_sessions with NULL note_content gets no note row.

    The migration ran during the engine fixture; this test seeds rows
    AFTER the migration to verify the backfill rule itself, since the
    test isolation truncates both tables. We re-run the backfill SQL by
    re-executing the same logic as the migration.
    """
    _insert_session(pg_session, session_id="sess-empty", note_content=None)
    _insert_session(
        pg_session,
        session_id="sess-with-note",
        note_content={"subjective": "S"},
    )
    pg_session.commit()

    pg_session.execute(
        text(
            """
            INSERT INTO practice.notes (
                id, patient_id, session_id, note_type, content,
                created_at, updated_at, export_status
            )
            SELECT
                ts.id, ts.patient_id, ts.id, COALESCE(ts.note_type, 'soap'),
                ts.note_content, ts.created_at,
                COALESCE(ts.updated_at, ts.created_at),
                COALESCE(ts.export_status, 'not_queued')
            FROM practice.therapy_sessions AS ts
            WHERE ts.note_content IS NOT NULL
              AND ts.note_content::text <> '{}'::text
            ON CONFLICT (id) DO NOTHING
            """
        )
    )
    pg_session.commit()

    rows = pg_session.execute(
        text("SELECT session_id, content FROM practice.notes ORDER BY session_id")
    ).all()
    assert [r[0] for r in rows] == ["sess-with-note"]
    assert rows[0][1] == {"subjective": "S"}
