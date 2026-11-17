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


class _RecordingComplianceStorage:
    """Records deletes by ``storage_uri``, the compliance backend's shape."""

    def __init__(self) -> None:
        self.deleted: list[str] = []

    def delete(self, uri: str) -> None:
        self.deleted.append(uri)


def _run_purge(
    schema: str,
    storage: _RecordingStorage,
    compliance: _RecordingComplianceStorage | None = None,
) -> int:
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
        patch(
            "app.jobs.hard_purge_cron._resolve_compliance_storage",
            return_value=compliance or _RecordingComplianceStorage(),
        ),
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


def _executed_patient_subplans(plan: dict[str, Any]) -> list[int]:
    """``Actual Loops`` of every subplan that reads ``patients``, anywhere in ``plan``."""
    loops: list[int] = []
    if plan.get("Subplan Name") and plan.get("Relation Name") == "patients":
        loops.append(int(plan.get("Actual Loops", 0)))
    for child in plan.get("Plans", ()):
        loops.extend(_executed_patient_subplans(child))
    return loops


def test_an_ordinary_read_never_runs_the_purge_subquery(engine: Engine, tenant_schema: str) -> None:
    """A connection that never set the purge setting skips the purge arm's subquery.

    The setting reads as NULL there, not ``''``. ``NULL = 'on' AND EXISTS (...)``
    cannot short-circuit, so the arm used to look each row's client up in
    ``patients`` (row security and all) on every ordinary read of these
    tables: about half the cost of a clinician counting their open notes.
    """
    from sqlalchemy import create_engine  # noqa: PLC0415
    from sqlalchemy.pool import NullPool  # noqa: PLC0415

    for hour in (9, 11, 13):
        _seed_client(
            engine, tenant_schema, clinician=_CLINICIAN_A, deleted_days_ago=None, hour=hour
        )

    # A connection of its own: a pooled one may have had the setting defined
    # by an earlier test, after which it reads as '' and the bug cannot show.
    fresh = create_engine(engine.url, poolclass=NullPool)
    try:
        with fresh.begin() as conn:
            _as(conn, tenant_schema, user_id=_CLINICIAN_A)
            unset = conn.execute(text("SELECT current_setting('app.allow_patient_purge', true)"))
            assert unset.scalar_one() is None, "precondition: the setting is NULL here"
            for table in ("notes", "therapy_sessions"):
                (plan,) = conn.execute(
                    text(f"EXPLAIN (ANALYZE, FORMAT JSON) SELECT count(*) FROM {table}")  # noqa: S608
                ).scalar_one()
                assert conn.execute(text(f"SELECT count(*) FROM {table}")).scalar_one() == 3  # noqa: S608
                loops = _executed_patient_subplans(plan["Plan"])
                assert loops, f"{table}: the purge arm's subplan is in the plan"
                assert loops == [0] * len(loops), f"{table}: purge subquery ran {loops} times"
    finally:
        fresh.dispose()


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


#: Tables the ``patients`` delete never reached (no foreign key, ``SET NULL``,
#: or a cascade a ``RESTRICT`` could block), plus the rows that hang off them.
_LEFT_BEHIND_TABLES = (
    "patient_intake_assignments",
    "patient_intake_responses",
    "patient_intake_submissions",
    "patient_charges",
    "patient_payment_methods",
    "companion_sessions",
    "companion_auth_challenges",
    "external_calendar_events",
    "patient_message_threads",
    "patient_messages",
    "patient_message_attachments",
    "patient_documents",
)


_INTAKE_FORM_TABLES = (
    "intake_packet_templates",
    "intake_packet_versions",
    "intake_item_definitions",
)


def _intake_form(engine: Engine, schema: str) -> dict[str, str]:
    """One practice intake form with one question. Not a client's row."""
    ids = {name: str(uuid.uuid4()) for name in ("template", "version", "item")}
    with engine.begin() as conn:
        _as(conn, schema)
        for table in _INTAKE_FORM_TABLES:
            conn.execute(text(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY"))
        conn.execute(
            text(
                "INSERT INTO intake_packet_templates (id, name, created_at) "
                "VALUES (CAST(:id AS uuid), 'New client', now())"
            ),
            {"id": ids["template"]},
        )
        conn.execute(
            text(
                "INSERT INTO intake_packet_versions (id, template_id, version, created_at) "
                "VALUES (CAST(:id AS uuid), CAST(:t AS uuid), 1, now())"
            ),
            {"id": ids["version"], "t": ids["template"]},
        )
        conn.execute(
            text(
                "INSERT INTO intake_item_definitions (id, version_id, key, position, item_type) "
                "VALUES (CAST(:id AS uuid), CAST(:v AS uuid), 'reason', 0, 'free_text')"
            ),
            {"id": ids["item"], "v": ids["version"]},
        )
        for table in _INTAKE_FORM_TABLES:
            conn.execute(text(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY"))
    return ids


#: One file attached to a task, as ``POST /api/tasks/{id}/documents`` writes it.
_COMPLIANCE_DOCUMENT_INSERT = (
    "INSERT INTO compliance_documents (id, compliance_item_id, filename, mime_type, "
    "size_bytes, storage_uri, document_type, uploaded_at, uploaded_by_user_id) "
    "VALUES (gen_random_uuid(), CAST({item} AS uuid), 'release.pdf', 'application/pdf', "
    "10, {uri}, 'other', now(), CAST(:u AS uuid))"
)


def _seed_left_behind_rows(
    engine: Engine, schema: str, client: dict[str, str], form: dict[str, str], clinician: str
) -> list[str]:
    """One of each row the purge used to leave; returns the client's task ids.

    Written with row security un-forced for the transaction: what is under
    test is the purge, not the paths that write these rows.
    """
    pid = client["patient"]
    ids = {
        name: str(uuid.uuid4())
        for name in (
            "assignment",
            "thread",
            "message",
            "document",
            "attachment",
            "task",
            "subtask",
        )
    }
    tables = (*_LEFT_BEHIND_TABLES, "tasks", "compliance_documents")
    statements = (
        "INSERT INTO patient_intake_assignments (id, patient_id, version_id, status, "
        "assigned_at, updated_at) VALUES (CAST(:assignment AS uuid), CAST(:pid AS uuid), "
        "CAST(:version AS uuid), 'submitted', now(), now())",
        "INSERT INTO patient_intake_responses (id, assignment_id, patient_id, item_id, value, "
        "created_at, updated_at) VALUES (gen_random_uuid(), CAST(:assignment AS uuid), "
        'CAST(:pid AS uuid), CAST(:item AS uuid), CAST(\'{"text": "an answer"}\' AS jsonb), '
        "now(), now())",
        "INSERT INTO patient_intake_submissions (id, patient_id, submitted_at, payload, "
        "created_by, created_at) VALUES (CAST(gen_random_uuid() AS text), CAST(:pid AS uuid), "
        "now(), CAST('{}' AS jsonb), :u, now())",
        "INSERT INTO patient_charges (id, patient_id, amount_cents, status, kind, method, "
        "created_by_user_id, created_at) VALUES (CAST(gen_random_uuid() AS text), "
        "CAST(:pid AS uuid), 15000, 'succeeded', 'session', 'card', :u, now())",
        "INSERT INTO patient_payment_methods (id, patient_id, stripe_customer_id, "
        "created_by_user_id, created_at) VALUES (CAST(gen_random_uuid() AS text), "
        "CAST(:pid AS uuid), 'cus_test', :u, now())",
        "INSERT INTO companion_sessions (jti, patient_id, issued_at, expires_at, "
        "chain_started_at) VALUES (CAST(gen_random_uuid() AS text), CAST(:pid AS uuid), "
        "now(), now() + interval '1 hour', now())",
        "INSERT INTO companion_auth_challenges (jti, patient_id, created_at, expires_at) "
        "VALUES (CAST(gen_random_uuid() AS text), CAST(:pid AS uuid), now(), "
        "now() + interval '10 minutes')",
        "INSERT INTO external_calendar_events (id, user_id, source, source_event_id, start_at, "
        "end_at, title, answer, patient_id) VALUES (gen_random_uuid(), CAST(:u AS uuid), "
        "'ical:test', CAST(gen_random_uuid() AS text), now(), now() + interval '50 minutes', "
        "'Session', 'client', CAST(:pid AS uuid))",
        "INSERT INTO patient_documents (id, patient_id, filename, mime_type, gcs_path, "
        "uploaded_by_patient_id) VALUES (CAST(:document AS uuid), CAST(:pid AS uuid), "
        "'form.pdf', 'application/pdf', :path, CAST(:pid AS uuid))",
        "INSERT INTO patient_message_threads (id, patient_id, status, created_at, "
        "last_message_at) VALUES (CAST(:thread AS uuid), CAST(:pid AS uuid), 'open', now(), now())",
        "INSERT INTO patient_messages (id, thread_id, patient_id, sender, body, created_at) "
        "VALUES (CAST(:message AS uuid), CAST(:thread AS uuid), CAST(:pid AS uuid), 'patient', "
        "'see attached', now())",
        "INSERT INTO patient_message_attachments (id, message_id, document_id, patient_id, "
        "created_at) VALUES (CAST(:attachment AS uuid), CAST(:message AS uuid), "
        "CAST(:document AS uuid), CAST(:pid AS uuid), now())",
        "INSERT INTO tasks (id, user_id, item_type, label, category, patient_id, created_at, "
        "updated_at) VALUES (CAST(:task AS uuid), CAST(:u AS uuid), 'task', "
        "'Send the release form', 'practice', CAST(:pid AS uuid), now(), now())",
        "INSERT INTO tasks (id, user_id, item_type, label, category, parent_task_id, "
        "created_at, updated_at) VALUES (CAST(:subtask AS uuid), CAST(:u AS uuid), 'task', "
        "'Print it', 'practice', CAST(:task AS uuid), now(), now())",
        _COMPLIANCE_DOCUMENT_INSERT.format(item=":task", uri=":task_uri"),
        _COMPLIANCE_DOCUMENT_INSERT.format(item=":subtask", uri=":subtask_uri"),
    )
    params = {
        **ids,
        "pid": pid,
        "u": clinician,
        "version": form["version"],
        "item": form["item"],
        "path": f"documents/{pid}/form.pdf",
        "task_uri": f"gs://compliance/{ids['task']}/release.pdf",
        "subtask_uri": f"gs://compliance/{ids['subtask']}/release.pdf",
    }
    with engine.begin() as conn:
        _as(conn, schema)
        for table in tables:
            conn.execute(text(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY"))
        for statement in statements:
            conn.execute(text(statement), params)
        for table in tables:
            conn.execute(text(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY"))
    return [ids["task"], ids["subtask"]]


def _left_behind(
    engine: Engine, schema: str, client: dict[str, str], task_ids: list[str]
) -> dict[str, int]:
    """Rows left per table, counted with row security un-forced (see ``_rows_left``).

    Tasks are counted by id, so a task the purge only detached still counts.
    """
    counts: dict[str, int] = {}
    with engine.begin() as conn:
        _as(conn, schema)
        for table in (*_LEFT_BEHIND_TABLES, "tasks", "compliance_documents"):
            conn.execute(text(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY"))
        for table in _LEFT_BEHIND_TABLES:
            counts[table] = int(
                conn.execute(
                    text(f"SELECT count(*) FROM {table} WHERE patient_id = :pid"),  # noqa: S608
                    {"pid": client["patient"]},
                ).scalar_one()
            )
        counts["tasks"] = int(
            conn.execute(
                text("SELECT count(*) FROM tasks WHERE id = ANY(CAST(:ids AS uuid[]))"),
                {"ids": task_ids},
            ).scalar_one()
        )
        counts["compliance_documents"] = int(
            conn.execute(
                text(
                    "SELECT count(*) FROM compliance_documents "
                    "WHERE compliance_item_id = ANY(CAST(:ids AS uuid[]))"
                ),
                {"ids": task_ids},
            ).scalar_one()
        )
        for table in (*_LEFT_BEHIND_TABLES, "tasks", "compliance_documents"):
            conn.execute(text(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY"))
    return counts


def _seed_compliance_task(
    engine: Engine, schema: str, client: dict[str, str], clinician: str
) -> dict[str, str]:
    """A compliance task linked to the client, with a file and a supervision row."""
    ids = {name: str(uuid.uuid4()) for name in ("task", "supervision")}
    params = {
        **ids,
        "pid": client["patient"],
        "u": clinician,
        "uri": f"gs://compliance/{ids['task']}/supervision-log.pdf",
    }
    tables = ("tasks", "compliance_documents", "supervision_relationships")
    with engine.begin() as conn:
        _as(conn, schema)
        for table in tables:
            conn.execute(text(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY"))
        conn.execute(
            text(
                "INSERT INTO tasks (id, user_id, item_type, label, category, patient_id, "
                "created_at, updated_at) VALUES (CAST(:task AS uuid), CAST(:u AS uuid), "
                "'supervision_review', 'Supervision review', 'compliance', "
                "CAST(:pid AS uuid), now(), now())"
            ),
            params,
        )
        conn.execute(
            text(_COMPLIANCE_DOCUMENT_INSERT.format(item=":task", uri=":uri")),
            params,
        )
        conn.execute(
            text(
                "INSERT INTO supervision_relationships (id, user_id, compliance_item_id, "
                "relationship_type, supervisor_name, status, created_at, updated_at) "
                "VALUES (CAST(:supervision AS uuid), CAST(:u AS uuid), CAST(:task AS uuid), "
                "'clinical', 'A supervisor', 'active', now(), now())"
            ),
            params,
        )
        for table in tables:
            conn.execute(text(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY"))
    return {**ids, "uri": params["uri"]}


def _compliance_task_left(engine: Engine, schema: str, seeded: dict[str, str]) -> dict[str, Any]:
    tables = ("tasks", "compliance_documents", "supervision_relationships")
    with engine.begin() as conn:
        _as(conn, schema)
        for table in tables:
            conn.execute(text(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY"))
        task = conn.execute(
            text("SELECT patient_id FROM tasks WHERE id = CAST(:t AS uuid)"),
            {"t": seeded["task"]},
        ).all()
        documents = conn.execute(
            text("SELECT count(*) FROM compliance_documents WHERE compliance_item_id = :t"),
            {"t": seeded["task"]},
        ).scalar_one()
        supervision = conn.execute(
            text("SELECT count(*) FROM supervision_relationships WHERE compliance_item_id = :t"),
            {"t": seeded["task"]},
        ).scalar_one()
        for table in tables:
            conn.execute(text(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY"))
    return {
        "task": [None if row[0] is None else str(row[0]) for row in task],
        "documents": int(documents),
        "supervision": int(supervision),
    }


def test_the_purge_leaves_none_of_the_rows_no_foreign_key_reached(
    engine: Engine, tenant_schema: str
) -> None:
    """Intake answers, charges, the card on file, portal sign-ins, calendar
    links, tasks and messages with an attachment go with the client.

    The attachment matters: it holds its document with ``ON DELETE RESTRICT``,
    and before the threads were deleted first, a client who had sent one could
    not be purged at all. The control client, not deleted, keeps every row.
    """
    form = _intake_form(engine, tenant_schema)
    old = _seed_client(engine, tenant_schema, clinician=_CLINICIAN_A, deleted_days_ago=45, hour=9)
    live = _seed_client(
        engine, tenant_schema, clinician=_CLINICIAN_A, deleted_days_ago=None, hour=13
    )
    old_tasks = _seed_left_behind_rows(engine, tenant_schema, old, form, _CLINICIAN_A)
    live_tasks = _seed_left_behind_rows(engine, tenant_schema, live, form, _CLINICIAN_A)
    old_compliance = _seed_compliance_task(engine, tenant_schema, old, _CLINICIAN_A)
    live_compliance = _seed_compliance_task(engine, tenant_schema, live, _CLINICIAN_A)
    compliance_storage = _RecordingComplianceStorage()

    assert _run_purge(tenant_schema, _RecordingStorage(), compliance_storage) == 0

    assert _left_behind(engine, tenant_schema, old, old_tasks) == dict.fromkeys(
        (*_LEFT_BEHIND_TABLES, "tasks", "compliance_documents"), 0
    )
    assert set(_rows_left(engine, tenant_schema, old).values()) == {0}
    # The files on the to-do and its step leave storage with their rows; the
    # compliance task's file is the clinician's and stays.
    assert sorted(compliance_storage.deleted) == sorted(
        f"gs://compliance/{task_id}/release.pdf" for task_id in old_tasks
    )
    # The compliance task is the clinician's record: kept, with its file and
    # its supervision relationship, and no longer linked to the client.
    assert _compliance_task_left(engine, tenant_schema, old_compliance) == {
        "task": [None],
        "documents": 1,
        "supervision": 1,
    }
    assert _left_behind(engine, tenant_schema, live, live_tasks) == {
        **dict.fromkeys(_LEFT_BEHIND_TABLES, 1),
        "tasks": 2,
        "compliance_documents": 2,
    }
    assert set(_rows_left(engine, tenant_schema, live).values()) == {1}
    assert _compliance_task_left(engine, tenant_schema, live_compliance) == {
        "task": [live["patient"]],
        "documents": 1,
        "supervision": 1,
    }
