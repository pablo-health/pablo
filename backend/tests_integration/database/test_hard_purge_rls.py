# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""The hard purge has to reach soft-deleted clients through row-level security.

The app role is NOSUPERUSER NOBYPASSRLS and every patient table is
force-RLS'd, so a job with no principal armed reads no patients at all. The
purge used to do exactly that: find nothing, delete nothing, exit 0. It now
arms ``app.allow_patient_purge`` (``SET LOCAL``) and the policies that setting
opens reach only soft-deleted clients and their rows.

These tests run the job's own ``run()`` against a real practice schema under
the suite's non-superuser role, rather than re-typing its SQL — a test that
re-types the DELETE would pass while production deleted nothing. Only the
practice list and the storage provider are stubbed.

Same fixture pattern as ``test_audit_retention_purge_rls``: schemas come from
``create_practice_schema`` (the tenant template), which is where the policies
live.
"""

from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any
from unittest.mock import patch

import pytest
from sqlalchemy import text

if TYPE_CHECKING:
    from collections.abc import Iterator

    from sqlalchemy.engine import Connection, Engine

_db_url = os.environ.get("DATABASE_URL", "")
pytestmark = pytest.mark.skipif(
    not _db_url or os.environ.get("DATABASE_BACKEND") != "postgres",
    reason=(
        "PostgreSQL not configured. Set DATABASE_URL and "
        "DATABASE_BACKEND=postgres; testcontainers should set both."
    ),
)

_CLINICIAN_A = "6a1f0c2e-7b3d-4e5f-8a9b-0c1d2e3f4a5b"
_CLINICIAN_B = "7b2e1d3f-8c4e-4f6a-9b0c-1d2e3f4a5b6c"
_PURGE_TABLES = (
    "patients",
    "appointments",
    "notes",
    "therapy_sessions",
    "chat_conversations",
    "patient_clinicians",
)


@pytest.fixture
def tenant_schema(engine: Engine) -> Iterator[str]:
    from app.db.provisioning import create_practice_schema  # noqa: PLC0415

    schema = f"practice_test_hard_purge_{uuid.uuid4().hex[:8]}"
    create_practice_schema(engine, schema)
    yield schema
    with engine.connect() as conn:
        conn.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        conn.commit()


def _as(conn: Connection, schema: str, *, user_id: str | None = None) -> None:
    conn.execute(text(f"SET LOCAL search_path = {schema}, platform, public"))
    if user_id is not None:
        conn.execute(text("SELECT set_config('app.current_user_id', :u, true)"), {"u": user_id})


def _seed_client(
    engine: Engine,
    schema: str,
    *,
    clinician: str,
    deleted_days_ago: int | None,
    hour: int,
) -> dict[str, str]:
    """A client with one of everything the purge deletes, written as its clinician."""
    ids = {name: str(uuid.uuid4()) for name in ("patient", "session", "conversation")}
    start = datetime(2026, 9, 1, hour, tzinfo=UTC)
    with engine.begin() as conn:
        _as(conn, schema, user_id=clinician)
        conn.execute(
            text(
                "INSERT INTO patients (id, first_name, last_name, first_name_lower, "
                "last_name_lower, date_of_birth, status, session_count, created_at, updated_at) "
                "VALUES (CAST(:pid AS uuid), 'Ada', 'Lovelace', 'ada', 'lovelace', "
                "'1815-12-10', 'active', 0, now(), now())"
            ),
            {"pid": ids["patient"]},
        )
        conn.execute(
            text(
                "INSERT INTO patient_clinicians (patient_id, user_id, granted_by) "
                "VALUES (CAST(:pid AS uuid), :u, :u)"
            ),
            {"pid": ids["patient"], "u": clinician},
        )
        conn.execute(
            text(
                "INSERT INTO therapy_sessions (id, user_id, patient_id, session_date, "
                "session_number, status, transcript, audio_gcs_path, created_at) "
                "VALUES (CAST(:id AS uuid), CAST(:u AS uuid), CAST(:pid AS uuid), :start, 1, "
                "'pending_review', CAST('{}' AS jsonb), :audio, now())"
            ),
            {
                "id": ids["session"],
                "u": clinician,
                "pid": ids["patient"],
                "start": start,
                "audio": f"audio/{ids['patient']}.wav",
            },
        )
        conn.execute(
            text(
                "INSERT INTO notes (id, patient_id, note_type, content, status, "
                "author_user_id, restricted, created_at, updated_at) "
                "VALUES (CAST(:id AS uuid), CAST(:pid AS uuid), 'narrative', "
                "CAST('{}' AS jsonb), 'draft', CAST(:u AS uuid), false, now(), now())"
            ),
            {"id": str(uuid.uuid4()), "pid": ids["patient"], "u": clinician},
        )
        conn.execute(
            text(
                "INSERT INTO appointments (id, user_id, patient_id, title, start_at, end_at, "
                "duration_minutes, status, session_type, is_exception, reminder_24h_sent, "
                "reminder_1h_sent, created_at) "
                "VALUES (CAST(:id AS uuid), CAST(:u AS uuid), CAST(:pid AS uuid), 'Session', "
                ":start, :end, 50, 'confirmed', 'individual', false, false, false, now())"
            ),
            {
                "id": str(uuid.uuid4()),
                "u": clinician,
                "pid": ids["patient"],
                "start": start,
                "end": start + timedelta(minutes=50),
            },
        )
        conn.execute(
            text(
                "INSERT INTO chat_conversations (id, patient_id, owner_user_id, title, "
                "caller_system_prompt, caller_feature_key, created_at) "
                "VALUES (CAST(:id AS uuid), CAST(:pid AS uuid), CAST(:u AS uuid), 'Chat', "
                "'You are helpful.', 'test', now())"
            ),
            {"id": ids["conversation"], "pid": ids["patient"], "u": clinician},
        )
        conn.execute(
            text(
                "INSERT INTO chat_messages (id, conversation_id, sequence, role, content, "
                "created_at) VALUES (CAST(:id AS uuid), CAST(:cid AS uuid), 1, 'user', "
                "'hello', now())"
            ),
            {"id": str(uuid.uuid4()), "cid": ids["conversation"]},
        )
        if deleted_days_ago is not None:
            conn.execute(
                text("UPDATE patients SET deleted_at = :at WHERE id = CAST(:pid AS uuid)"),
                {
                    "at": datetime.now(UTC) - timedelta(days=deleted_days_ago),
                    "pid": ids["patient"],
                },
            )
    return ids


def _rows_left(engine: Engine, schema: str, client: dict[str, str]) -> dict[str, int]:
    """Count without a policy in the way: RLS un-forced for one transaction.

    The question is what the purge left behind, and asking it through the
    policies that hid rows from the purge would answer it wrong.
    """
    counts: dict[str, int] = {}
    with engine.begin() as conn:
        for table in (*_PURGE_TABLES, "chat_messages"):
            conn.execute(text(f"ALTER TABLE {schema}.{table} NO FORCE ROW LEVEL SECURITY"))
        for table in _PURGE_TABLES:
            column = "id" if table == "patients" else "patient_id"
            counts[table] = int(
                conn.execute(
                    text(f"SELECT count(*) FROM {schema}.{table} WHERE {column} = :pid"),  # noqa: S608
                    {"pid": client["patient"]},
                ).scalar_one()
            )
        counts["chat_messages"] = int(
            conn.execute(
                text(f"SELECT count(*) FROM {schema}.chat_messages WHERE conversation_id = :cid"),  # noqa: S608
                {"cid": client["conversation"]},
            ).scalar_one()
        )
        for table in (*_PURGE_TABLES, "chat_messages"):
            conn.execute(text(f"ALTER TABLE {schema}.{table} FORCE ROW LEVEL SECURITY"))
    return counts


class _RecordingStorage:
    """Records deletes; holds no objects, so listing finds nothing."""

    def __init__(self) -> None:
        self.deleted: list[str] = []
        self.listed: list[str] = []

    def delete(self, *, bucket: str, object_name: str) -> None:
        self.deleted.append(f"{bucket}/{object_name}")

    def list_names(self, *, bucket: str, prefix: str) -> list[str]:
        self.listed.append(f"{bucket}/{prefix}")
        return []


def _run_purge(schema: str, storage: _RecordingStorage) -> int:
    from app.jobs import hard_purge_cron  # noqa: PLC0415
    from app.jobs.hard_purge_retention_stub import (  # noqa: PLC0415
        register_compliance_retention_stub_writer,
    )

    register_compliance_retention_stub_writer(None)
    with (
        patch(
            "app.jobs.hard_purge_cron.list_active_practice_registry",
            return_value=[(schema, "practice-test")],
        ),
        patch(
            "app.jobs.hard_purge_cron._resolve_audio_storage",
            return_value=(storage, "audio-bucket"),
        ),
        patch("app.jobs.hard_purge_cron._resolve_documents_storage", return_value=None),
    ):
        return hard_purge_cron.run([])


def test_a_client_deleted_over_30_days_ago_is_purged_and_no_other(
    engine: Engine, tenant_schema: str
) -> None:
    old = _seed_client(engine, tenant_schema, clinician=_CLINICIAN_A, deleted_days_ago=45, hour=9)
    recent = _seed_client(
        engine, tenant_schema, clinician=_CLINICIAN_A, deleted_days_ago=10, hour=11
    )
    live = _seed_client(
        engine, tenant_schema, clinician=_CLINICIAN_A, deleted_days_ago=None, hour=13
    )
    storage = _RecordingStorage()

    assert _run_purge(tenant_schema, storage) == 0

    assert set(_rows_left(engine, tenant_schema, old).values()) == {0}
    assert storage.deleted == [f"audio-bucket/audio/{old['patient']}.wav"]
    for kept in (recent, live):
        assert set(_rows_left(engine, tenant_schema, kept).values()) == {1}


def test_the_purge_leaves_one_audit_entry_with_no_name_or_birth_date(
    engine: Engine, tenant_schema: str
) -> None:
    old = _seed_client(engine, tenant_schema, clinician=_CLINICIAN_A, deleted_days_ago=45, hour=9)

    assert _run_purge(tenant_schema, _RecordingStorage()) == 0

    with engine.begin() as conn:
        _as(conn, tenant_schema, user_id="system:retention_job")
        rows = (
            conn.execute(
                text("SELECT * FROM audit_logs WHERE action = 'patient_purged'"),
            )
            .mappings()
            .all()
        )
    assert len(rows) == 1
    entry: dict[str, Any] = dict(rows[0])
    assert entry["resource_id"] == old["patient"]
    assert str(entry["patient_id"]) == old["patient"]
    assert entry["actor_type"] == "system"
    flattened = " ".join(str(value) for value in entry.values()).lower()
    for phi in ("ada", "lovelace", "1815"):
        assert phi not in flattened


def test_without_the_setting_nothing_is_visible(engine: Engine, tenant_schema: str) -> None:
    _seed_client(engine, tenant_schema, clinician=_CLINICIAN_A, deleted_days_ago=45, hour=9)
    _seed_client(engine, tenant_schema, clinician=_CLINICIAN_A, deleted_days_ago=None, hour=13)

    with engine.begin() as conn:
        _as(conn, tenant_schema)
        for table in (*_PURGE_TABLES, "chat_messages"):
            seen = conn.execute(text(f"SELECT count(*) FROM {table}")).scalar_one()  # noqa: S608
            assert seen == 0, f"{table} visible with no principal and no purge setting"


def test_the_setting_reaches_soft_deleted_clients_only(engine: Engine, tenant_schema: str) -> None:
    old = _seed_client(engine, tenant_schema, clinician=_CLINICIAN_A, deleted_days_ago=45, hour=9)
    recent = _seed_client(
        engine, tenant_schema, clinician=_CLINICIAN_A, deleted_days_ago=10, hour=11
    )
    _seed_client(engine, tenant_schema, clinician=_CLINICIAN_A, deleted_days_ago=None, hour=13)

    with engine.begin() as conn:
        _as(conn, tenant_schema)
        conn.execute(text("SET LOCAL app.allow_patient_purge = 'on'"))
        patients = {str(r[0]) for r in conn.execute(text("SELECT id FROM patients"))}
        appointments = {
            str(r[0]) for r in conn.execute(text("SELECT patient_id FROM appointments"))
        }

    assert patients == {old["patient"], recent["patient"]}
    assert appointments == {old["patient"], recent["patient"]}


def test_the_setting_does_not_widen_a_clinicians_session(
    engine: Engine, tenant_schema: str
) -> None:
    """Clinician B never sees A's current clients, armed or not; A's own view is unchanged."""
    _seed_client(engine, tenant_schema, clinician=_CLINICIAN_A, deleted_days_ago=None, hour=9)
    _seed_client(engine, tenant_schema, clinician=_CLINICIAN_B, deleted_days_ago=None, hour=11)

    def visible(user_id: str, *, armed: bool) -> dict[str, int]:
        with engine.begin() as conn:
            _as(conn, tenant_schema, user_id=user_id)
            if armed:
                conn.execute(text("SET LOCAL app.allow_patient_purge = 'on'"))
            return {
                table: int(conn.execute(text(f"SELECT count(*) FROM {table}")).scalar_one())  # noqa: S608
                for table in ("patients", "appointments", "notes", "chat_conversations")
            }

    for user_id in (_CLINICIAN_A, _CLINICIAN_B):
        assert visible(user_id, armed=False) == visible(user_id, armed=True)
        assert set(visible(user_id, armed=False).values()) == {1}
