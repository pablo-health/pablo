# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""A live grant on a client reads that client's sessions and appointments.

Runs as the ``pablo`` role the integration conftest creates ``NOSUPERUSER
NOBYPASSRLS``, so every assertion here is RLS's answer. Run via
``make test-integration``.
"""

from __future__ import annotations

import os
import uuid
from contextlib import contextmanager
from datetime import UTC, date, datetime, time, timedelta
from types import SimpleNamespace
from typing import TYPE_CHECKING

import pytest
from app.db.migrate_tenants import reconcile_tenant_rls
from app.db.provisioning import create_practice_schema
from app.migration.apply import ApplyReport, ArchiveApplier, _Run
from app.migration.readers.simplepractice import AppointmentInfo
from sqlalchemy import text
from sqlalchemy.orm import Session as OrmSession

if TYPE_CHECKING:
    from collections.abc import Iterator

    from app.scheduling_engine.models.appointment import Appointment
    from sqlalchemy.engine import Connection, Engine


pytestmark = pytest.mark.skipif(
    not os.environ.get("DATABASE_URL") or os.environ.get("DATABASE_BACKEND") != "postgres",
    reason="PostgreSQL not configured; testcontainers sets DATABASE_URL and DATABASE_BACKEND.",
)


_GRANT_READ_TABLES = ("therapy_sessions", "appointments")


def _armed(conn: Connection, schema: str, user_id: str) -> None:
    conn.execute(text(f"SET LOCAL search_path = {schema}, platform, public"))
    conn.execute(text("SELECT set_config('app.current_user_id', :u, true)"), {"u": user_id})


def _seed_client_of(engine: Engine, schema: str, owner: str) -> str:
    """A client with ``owner``'s primary grant, one session and one appointment."""
    patient_id = str(uuid.uuid4())
    with engine.begin() as conn:
        _armed(conn, schema, owner)
        conn.execute(
            text(
                "INSERT INTO patients (id, first_name, last_name, first_name_lower, "
                "last_name_lower, status, session_count, created_at, updated_at) "
                "VALUES (CAST(:pid AS uuid), 'Ada', 'Lovelace', 'ada', 'lovelace', "
                "'active', 0, now(), now())"
            ),
            {"pid": patient_id},
        )
        conn.execute(
            text(
                "INSERT INTO patient_clinicians (patient_id, user_id, granted_by) "
                "VALUES (CAST(:pid AS uuid), :u, :u)"
            ),
            {"pid": patient_id, "u": owner},
        )
        conn.execute(
            text(
                "INSERT INTO therapy_sessions (id, user_id, patient_id, session_date, "
                "session_number, status, transcript, created_at) "
                "VALUES (gen_random_uuid(), :u, CAST(:pid AS uuid), now(), 1, "
                "'scheduled', '{}'::jsonb, now())"
            ),
            {"pid": patient_id, "u": owner},
        )
    # One owner's active appointments may not overlap.
    hours = uuid.UUID(patient_id).int % 100_000
    _appointment_at(engine, schema, owner, patient_id, datetime.now(UTC) + timedelta(hours=hours))
    return patient_id


def _appointment_at(
    engine: Engine, schema: str, owner: str, patient_id: str, start: datetime
) -> str:
    with engine.begin() as conn:
        _armed(conn, schema, owner)
        return str(
            conn.execute(
                text(
                    "INSERT INTO appointments (id, user_id, patient_id, title, start_at, "
                    "end_at, duration_minutes, status, session_type, is_exception, "
                    "reminder_24h_sent, reminder_1h_sent, created_at) "
                    "VALUES (gen_random_uuid(), :u, CAST(:pid AS uuid), 'Session', :s, "
                    ":s + interval '50 minutes', 50, 'completed', 'individual', false, "
                    "false, false, now()) RETURNING id"
                ),
                {"pid": patient_id, "u": owner, "s": start},
            ).scalar_one()
        )


def _grant(engine: Engine, schema: str, patient_id: str, user_id: str, *, expired: bool) -> None:
    with engine.begin() as conn:
        # The grant table's own policy admits only the grantee's row.
        _armed(conn, schema, user_id)
        conn.execute(
            text(
                "INSERT INTO patient_clinicians (patient_id, user_id, role, granted_by, "
                "expires_at) VALUES (CAST(:pid AS uuid), :u, 'co_treating', :u, "
                "CASE WHEN :expired THEN now() - interval '1 day' END)"
            ),
            {"pid": patient_id, "u": user_id, "expired": expired},
        )


def _visible(engine: Engine, schema: str, reader: str, table: str, patient_id: str) -> int:
    with engine.begin() as conn:
        _armed(conn, schema, reader)
        count = conn.execute(
            text(f"SELECT count(*) FROM {table} WHERE patient_id = CAST(:pid AS uuid)"),  # noqa: S608
            {"pid": patient_id},
        ).scalar_one()
    return int(count)


class TestGrantReadsSessionsAndAppointments:
    """A live grant on a client reads that client's sessions and appointments.

    Writes stay with the row's own clinician: the read is a separate
    SELECT-only policy, and the owner's policy still gates every write.
    """

    @pytest.mark.parametrize("table", _GRANT_READ_TABLES)
    def test_live_grant_reads_and_expired_grant_does_not(
        self, engine: Engine, tenant_schema: str, table: str
    ) -> None:
        owner, colleague = str(uuid.uuid4()), str(uuid.uuid4())
        live = _seed_client_of(engine, tenant_schema, owner)
        expired = _seed_client_of(engine, tenant_schema, owner)
        _grant(engine, tenant_schema, live, colleague, expired=False)
        _grant(engine, tenant_schema, expired, colleague, expired=True)

        assert _visible(engine, tenant_schema, colleague, table, live) == 1
        assert _visible(engine, tenant_schema, colleague, table, expired) == 0
        assert _visible(engine, tenant_schema, owner, table, live) == 1

    @pytest.mark.parametrize("table", _GRANT_READ_TABLES)
    def test_no_grant_reads_only_own_rows(
        self, engine: Engine, tenant_schema: str, table: str
    ) -> None:
        owner, stranger = str(uuid.uuid4()), str(uuid.uuid4())
        theirs = _seed_client_of(engine, tenant_schema, owner)
        mine = _seed_client_of(engine, tenant_schema, stranger)

        assert _visible(engine, tenant_schema, stranger, table, theirs) == 0
        assert _visible(engine, tenant_schema, stranger, table, mine) == 1

    @pytest.mark.parametrize("granted", [True, False])
    @pytest.mark.parametrize("table", _GRANT_READ_TABLES)
    def test_colleague_cannot_update_or_delete_the_owners_rows(
        self, engine: Engine, tenant_schema: str, table: str, granted: bool
    ) -> None:
        owner, colleague = str(uuid.uuid4()), str(uuid.uuid4())
        patient_id = _seed_client_of(engine, tenant_schema, owner)
        if granted:
            _grant(engine, tenant_schema, patient_id, colleague, expired=False)

        where = "WHERE patient_id = CAST(:pid AS uuid)"
        with engine.begin() as conn:
            _armed(conn, tenant_schema, colleague)
            updated = conn.execute(
                text(f"UPDATE {table} SET updated_at = now() {where}"),  # noqa: S608
                {"pid": patient_id},
            ).rowcount
            deleted = conn.execute(
                text(f"DELETE FROM {table} {where}"),  # noqa: S608
                {"pid": patient_id},
            ).rowcount

        assert (updated, deleted) == (0, 0)
        assert _visible(engine, tenant_schema, owner, table, patient_id) == 1

    def test_reconcile_gives_an_existing_schema_the_grant_read(self, engine: Engine) -> None:
        """A practice provisioned before the policy gets it from the migrate fan-out."""
        with _fresh_schema(engine) as schema:
            owner, colleague = str(uuid.uuid4()), str(uuid.uuid4())
            patient_id = _seed_client_of(engine, schema, owner)
            _grant(engine, schema, patient_id, colleague, expired=False)
            with engine.begin() as conn:
                for table in _GRANT_READ_TABLES:
                    conn.execute(text(f"DROP POLICY rls_grant_read ON {schema}.{table}"))
            for table in _GRANT_READ_TABLES:
                assert _visible(engine, schema, colleague, table, patient_id) == 0

            reconcile_tenant_rls(engine, schema)

            for table in _GRANT_READ_TABLES:
                assert _visible(engine, schema, colleague, table, patient_id) == 1

    def test_reconcile_rebuilds_a_table_that_still_holds_the_grant_read(
        self, engine: Engine
    ) -> None:
        """A damaged owner policy is rebuilt, and ``rls_grant_read`` still admits the colleague.

        The pass judges a policy by its text, so the owner policy is edited to
        admit nothing; the comment alone is bookkeeping and changes nothing.
        """
        with _fresh_schema(engine) as schema:
            owner, colleague = str(uuid.uuid4()), str(uuid.uuid4())
            patient_id = _seed_client_of(engine, schema, owner)
            _grant(engine, schema, patient_id, colleague, expired=False)
            with engine.begin() as conn:
                for table in _GRANT_READ_TABLES:
                    conn.execute(
                        text(f"ALTER POLICY rls_user_isolation ON {schema}.{table} USING (false)")
                    )

            counts = reconcile_tenant_rls(engine, schema)

            assert counts.altered == len(_GRANT_READ_TABLES)
            for table in _GRANT_READ_TABLES:
                assert _visible(engine, schema, colleague, table, patient_id) == 1


@contextmanager
def _fresh_schema(engine: Engine) -> Iterator[str]:
    schema = f"practice_test_grant_{uuid.uuid4().hex[:8]}"
    create_practice_schema(engine, schema)
    try:
        yield schema
    finally:
        with engine.begin() as conn:
            conn.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))


@pytest.fixture(scope="module")
def tenant_schema(engine: Engine) -> Iterator[str]:
    with _fresh_schema(engine) as schema:
        yield schema


class TestImportVisitLookup:
    """An imported note's visit is looked up on its author's calendar only.

    A grant reads the colleague's appointments too, and a co-therapy visit
    there at the same start is not the author's.
    """

    _INFO = AppointmentInfo(
        kind="Individual",
        on=date(2031, 3, 4),
        start=time(10, 0),
        end=time(10, 50),
        duration_minutes=50,
        zone="",
        billing_code=None,
        billing_description=None,
    )

    def _visit_for(
        self, engine: Engine, schema: str, author: str, patient_id: str
    ) -> tuple[str | None, list[Appointment]]:
        created: list[Appointment] = []

        def create(appointment: Appointment) -> Appointment:
            created.append(appointment)
            return appointment

        applier = ArchiveApplier.__new__(ArchiveApplier)
        applier._appointments = SimpleNamespace(create=create)  # type: ignore[assignment]
        applier._ledger = lambda *_: None  # type: ignore[method-assign]
        note = SimpleNamespace(appointment=self._INFO, source_id="note-1", digest="d")
        run = _Run(
            run_id="run-1",
            archive=SimpleNamespace(),  # type: ignore[arg-type]
            decisions={},
            providers={},
            landable={},
            report=ApplyReport(),
        )
        with engine.begin() as conn:
            _armed(conn, schema, author)
            applier._session = OrmSession(bind=conn)
            visit = applier._visit_for(run, note, patient_id, author)  # type: ignore[arg-type]
        return visit, created

    def test_co_therapy_finds_the_authors_own_visit(
        self, engine: Engine, tenant_schema: str
    ) -> None:
        owner, author = str(uuid.uuid4()), str(uuid.uuid4())
        patient_id = _seed_client_of(engine, tenant_schema, owner)
        _grant(engine, tenant_schema, patient_id, author, expired=False)
        starts = self._INFO.starts_at
        _appointment_at(engine, tenant_schema, owner, patient_id, starts)
        mine = _appointment_at(engine, tenant_schema, author, patient_id, starts)

        assert self._visit_for(engine, tenant_schema, author, patient_id) == (mine, [])

    def test_a_colleagues_visit_is_not_reused(self, engine: Engine, tenant_schema: str) -> None:
        owner, author = str(uuid.uuid4()), str(uuid.uuid4())
        patient_id = _seed_client_of(engine, tenant_schema, owner)
        _grant(engine, tenant_schema, patient_id, author, expired=False)
        _appointment_at(engine, tenant_schema, owner, patient_id, self._INFO.starts_at)

        visit, created = self._visit_for(engine, tenant_schema, author, patient_id)

        assert [a.user_id for a in created] == [author]
        assert visit == created[0].id
