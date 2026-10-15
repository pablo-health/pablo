# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""The practice owner reads the activity on their clients' records, and no more.

``rls_audit_owner_client_read`` is the one place a clinician reads audit
rows they did not write. Everything here runs against a schema built by
the real ``create_practice_schema`` (so the policy is the one the template
ships), under the integration ``pablo`` role, which is NOSUPERUSER
NOBYPASSRLS — the same posture as production.

The cast:

* the OWNER, recorded as ``platform.practices.owner_user_id`` for this
  schema, with access to client A only;
* a COLLEAGUE, with access to clients A and B, who owns nothing;
* client A, acting for themselves through the portal.

What has to hold, each in both directions so no assertion is vacuous:

1. The owner reads the colleague's and client A's rows about client A.
2. The owner does not read rows about client B, which they cannot access,
   nor rows about no client at all.
3. The colleague, who is not the owner, still reads only their own rows.
4. Owning a *different* practice widens nothing here.
5. The policy adds reads only: the owner cannot UPDATE or DELETE a row it
   now reads, nor INSERT one in someone else's name.
"""

from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

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

_OWNER = str(uuid.uuid4())
_COLLEAGUE = str(uuid.uuid4())


class _Cast:
    def __init__(self, schema: str, client_a: str, client_b: str, rows: dict[str, str]) -> None:
        self.schema = schema
        self.client_a = client_a
        self.client_b = client_b
        self.rows = rows


def _arm(conn: Connection, schema: str, *, user_id: str | None = None) -> None:
    conn.execute(text(f"SET search_path = {schema}, platform, public"))
    conn.execute(text("SELECT set_config('app.current_user_id', :u, false)"), {"u": user_id or ""})
    conn.execute(text("SELECT set_config('app.current_patient_id', '', false)"))


def _register_practice(conn: Connection, schema: str, owner: str) -> str:
    practice_id = f"test-practice-{uuid.uuid4().hex[:8]}"
    conn.execute(
        text(
            "INSERT INTO platform.practices"
            " (id, name, schema_name, owner_email, owner_user_id, product, status,"
            "  is_active, created_at)"
            " VALUES (:id, 'Activity Practice', :schema, 'owner@example.com',"
            "         CAST(:owner AS uuid), 'pablo', 'active', TRUE, now())"
        ),
        {"id": practice_id, "schema": schema, "owner": owner},
    )
    return practice_id


def _write_row(
    conn: Connection,
    schema: str,
    *,
    actor_type: str,
    actor_id: str,
    patient_id: str | None,
) -> str:
    """Write one row through the actor policy, as its actor would."""
    row_id = str(uuid.uuid4())
    guc = "app.current_patient_id" if actor_type == "patient" else "app.current_user_id"
    conn.execute(text(f"SET search_path = {schema}, platform, public"))
    conn.execute(text(f"SELECT set_config('{guc}', :a, false)"), {"a": actor_id})
    conn.execute(
        text(
            'INSERT INTO audit_logs (id, "timestamp", expires_at, user_id, actor_type,'
            " action, resource_type, resource_id, patient_id)"
            " VALUES (CAST(:id AS uuid), :ts, :exp, :user_id, :actor_type,"
            "         'patient_viewed', 'patient', :res, CAST(:patient_id AS uuid))"
        ),
        {
            "id": row_id,
            "ts": datetime.now(UTC),
            "exp": datetime.now(UTC) + timedelta(days=2555),
            "user_id": actor_id,
            "actor_type": actor_type,
            "res": patient_id or actor_id,
            "patient_id": patient_id,
        },
    )
    conn.execute(text(f"SELECT set_config('{guc}', '', false)"))
    return row_id


@pytest.fixture(scope="module")
def cast(engine: Engine) -> Iterator[_Cast]:
    from app.db.provisioning import create_practice_schema  # noqa: PLC0415

    with engine.connect() as conn:
        conn.execute(text("SET search_path = practice, platform, public"))
        conn.commit()

    schema = f"practice_test_owner_read_{uuid.uuid4().hex[:8]}"
    create_practice_schema(engine, schema)
    client_a, client_b = str(uuid.uuid4()), str(uuid.uuid4())

    with engine.begin() as conn:
        for user_id, name in ((_OWNER, "Owner"), (_COLLEAGUE, "Colleague")):
            conn.execute(
                text(
                    "INSERT INTO platform.users (id, email, name, created_at, status,"
                    " is_platform_admin, chat_quality_review_opt_in,"
                    " session_notes_quality_review_opt_in, inbox_quality_review_opt_in)"
                    " VALUES (CAST(:u AS uuid), :email, :name, now(), 'approved', false,"
                    " false, false, false)"
                ),
                {"u": user_id, "email": f"{user_id}@example.com", "name": name},
            )
        practice_id = _register_practice(conn, schema, _OWNER)

        _arm(conn, schema, user_id=_COLLEAGUE)
        for pid in (client_a, client_b):
            conn.execute(
                text(
                    "INSERT INTO patients (id, first_name, last_name, "
                    "first_name_lower, last_name_lower, status, "
                    "session_count, created_at, updated_at) "
                    "VALUES (CAST(:pid AS uuid), 'Test', 'Client', "
                    "'test', 'client', 'active', 0, now(), now())"
                ),
                {"pid": pid},
            )
        # Each grant is written as its grantee: a clinician writes only their
        # own rows in patient_clinicians.
        grants = ((client_a, _OWNER), (client_a, _COLLEAGUE), (client_b, _COLLEAGUE))
        for pid, user_id in grants:
            _arm(conn, schema, user_id=user_id)
            conn.execute(
                text(
                    "INSERT INTO patient_clinicians (patient_id, user_id, granted_by) "
                    "VALUES (CAST(:pid AS uuid), :u, :u)"
                ),
                {"pid": pid, "u": user_id},
            )

        rows = {
            "colleague_on_a": _write_row(
                conn, schema, actor_type="clinician", actor_id=_COLLEAGUE, patient_id=client_a
            ),
            "client_a_self": _write_row(
                conn, schema, actor_type="patient", actor_id=client_a, patient_id=client_a
            ),
            "colleague_on_b": _write_row(
                conn, schema, actor_type="clinician", actor_id=_COLLEAGUE, patient_id=client_b
            ),
            "colleague_no_client": _write_row(
                conn, schema, actor_type="clinician", actor_id=_COLLEAGUE, patient_id=None
            ),
            "owner_on_a": _write_row(
                conn, schema, actor_type="clinician", actor_id=_OWNER, patient_id=client_a
            ),
        }

    yield _Cast(schema, client_a, client_b, rows)

    with engine.begin() as conn:
        conn.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        conn.execute(text("DELETE FROM platform.practices WHERE id = :id"), {"id": practice_id})
        conn.execute(
            text("DELETE FROM platform.users WHERE id IN (CAST(:a AS uuid), CAST(:b AS uuid))"),
            {"a": _OWNER, "b": _COLLEAGUE},
        )


def _visible(engine: Engine, schema: str, user_id: str) -> set[str]:
    with engine.begin() as conn:
        _arm(conn, schema, user_id=user_id)
        return {str(r) for r in conn.execute(text("SELECT id FROM audit_logs")).scalars()}


class TestWhatTheOwnerReads:
    def test_the_owner_reads_another_clinicians_and_the_clients_rows(
        self, engine: Engine, cast: _Cast
    ) -> None:
        visible = _visible(engine, cast.schema, _OWNER)

        assert cast.rows["colleague_on_a"] in visible
        assert cast.rows["client_a_self"] in visible
        assert cast.rows["owner_on_a"] in visible

    def test_not_for_a_client_the_owner_cannot_access(self, engine: Engine, cast: _Cast) -> None:
        # Control: the row exists and its own actor reads it.
        assert cast.rows["colleague_on_b"] in _visible(engine, cast.schema, _COLLEAGUE)

        assert cast.rows["colleague_on_b"] not in _visible(engine, cast.schema, _OWNER)

    def test_not_a_row_about_no_client(self, engine: Engine, cast: _Cast) -> None:
        assert cast.rows["colleague_no_client"] in _visible(engine, cast.schema, _COLLEAGUE)

        assert cast.rows["colleague_no_client"] not in _visible(engine, cast.schema, _OWNER)

    def test_the_repository_returns_every_actors_rows_for_the_client(
        self, engine: Engine, cast: _Cast
    ) -> None:
        """The route reads through ``list_for_patient``, which has no actor
        filter of its own — so this is the policy deciding, end to end."""
        from app.repositories.postgres.audit import PostgresAuditRepository  # noqa: PLC0415
        from sqlalchemy.orm import Session  # noqa: PLC0415

        with Session(bind=engine) as session:
            _arm(session.connection(), cast.schema, user_id=_OWNER)
            entries = PostgresAuditRepository(session).list_for_patient(cast.client_a)

        assert {e.id for e in entries} == {
            cast.rows["colleague_on_a"],
            cast.rows["client_a_self"],
            cast.rows["owner_on_a"],
        }
        assert {e.actor_type for e in entries} == {"clinician", "patient"}


class TestTheCallersSearchPathDoesNotMatter:
    """The policy is evaluated wherever an audit row is read, including the
    RETURNING of an INSERT, and some audit writes happen from a session that
    is not scoped to the tenant's search_path. A policy that resolved any
    name through the search_path would raise there — and a caller that
    swallows audit failures would then silently stop recording."""

    def test_an_insert_returning_from_an_unscoped_session_succeeds(
        self, engine: Engine, cast: _Cast
    ) -> None:
        row_id = str(uuid.uuid4())
        with engine.begin() as conn:
            conn.execute(text("SET search_path = public"))
            conn.execute(text("SELECT set_config('app.current_user_id', :u, false)"), {"u": _OWNER})
            returned = conn.execute(
                text(
                    f'INSERT INTO {cast.schema}.audit_logs (id, "timestamp", expires_at,'  # noqa: S608
                    " user_id, actor_type, action, resource_type, resource_id, patient_id)"
                    " VALUES (CAST(:id AS uuid), now(), now() + interval '1 day', :u,"
                    " 'clinician', 'patient_viewed', 'patient', 'x', CAST(:p AS uuid))"
                    " RETURNING id"
                ),
                {"id": row_id, "u": _OWNER, "p": cast.client_a},
            ).scalar_one()
            conn.execute(text("SELECT set_config('app.current_user_id', '', false)"))

        assert str(returned) == row_id

    def test_has_patient_access_reads_its_own_schemas_grants(
        self, engine: Engine, cast: _Cast
    ) -> None:
        """The policy calls the function rather than repeating it, so the
        function itself has to be search_path-independent."""
        with engine.begin() as conn:
            conn.execute(text("SET search_path = public"))
            conn.execute(text("SELECT set_config('app.current_user_id', :u, false)"), {"u": _OWNER})
            answers = conn.execute(
                text(
                    f"SELECT {cast.schema}.has_patient_access(CAST(:a AS uuid), :u),"
                    f" {cast.schema}.has_patient_access(CAST(:b AS uuid), :u)"
                ),
                {"a": cast.client_a, "b": cast.client_b, "u": _OWNER},
            ).one()
            conn.execute(text("SELECT set_config('app.current_user_id', '', false)"))

        assert tuple(answers) == (True, False)

    def test_an_unscoped_read_still_sees_what_the_owner_may_see(
        self, engine: Engine, cast: _Cast
    ) -> None:
        with engine.begin() as conn:
            conn.execute(text("SET search_path = public"))
            conn.execute(text("SELECT set_config('app.current_user_id', :u, false)"), {"u": _OWNER})
            visible = {
                str(r)
                for r in conn.execute(
                    text(f"SELECT id FROM {cast.schema}.audit_logs")  # noqa: S608
                ).scalars()
            }
            conn.execute(text("SELECT set_config('app.current_user_id', '', false)"))

        assert cast.rows["client_a_self"] in visible
        assert cast.rows["colleague_on_b"] not in visible


class TestNobodyElseGainsAnything:
    def test_a_clinician_who_is_not_the_owner_reads_only_their_own_rows(
        self, engine: Engine, cast: _Cast
    ) -> None:
        visible = _visible(engine, cast.schema, _COLLEAGUE)

        # The colleague has access to client A, and still does not read the
        # client's own rows or the owner's: access alone widens nothing.
        assert cast.rows["client_a_self"] not in visible
        assert cast.rows["owner_on_a"] not in visible
        assert cast.rows["colleague_on_a"] in visible

    def test_owning_another_practice_widens_nothing_here(self, engine: Engine, cast: _Cast) -> None:
        """The policy binds the owner to the practice whose schema holds
        the table. A colleague who owns some other practice is still only
        a colleague in this one."""
        with engine.begin() as conn:
            elsewhere = _register_practice(
                conn, f"practice_test_elsewhere_{uuid.uuid4().hex[:8]}", _COLLEAGUE
            )
        try:
            visible = _visible(engine, cast.schema, _COLLEAGUE)
        finally:
            with engine.begin() as conn:
                conn.execute(
                    text("DELETE FROM platform.practices WHERE id = :id"), {"id": elsewhere}
                )

        assert cast.rows["client_a_self"] not in visible
        assert cast.rows["owner_on_a"] not in visible

    def test_an_unarmed_session_reads_nothing(self, engine: Engine, cast: _Cast) -> None:
        with engine.begin() as conn:
            _arm(conn, cast.schema)
            assert conn.execute(text("SELECT count(*) FROM audit_logs")).scalar_one() == 0


class TestTheOwnerReadsAndNothingMore:
    def test_the_owner_cannot_update_a_row_it_reads(self, engine: Engine, cast: _Cast) -> None:
        with engine.begin() as conn:
            _arm(conn, cast.schema, user_id=_OWNER)
            result = conn.execute(
                text("UPDATE audit_logs SET action = 'tampered' WHERE id = CAST(:id AS uuid)"),
                {"id": cast.rows["colleague_on_a"]},
            )
            # No UPDATE policy admits the row, so the statement matches
            # nothing; the append-only trigger never even fires.
            assert result.rowcount == 0

        with engine.begin() as conn:
            _arm(conn, cast.schema, user_id=_COLLEAGUE)
            action = conn.execute(
                text("SELECT action FROM audit_logs WHERE id = CAST(:id AS uuid)"),
                {"id": cast.rows["colleague_on_a"]},
            ).scalar_one()
        assert action == "patient_viewed"

    def test_the_owner_cannot_delete_a_row_it_reads(self, engine: Engine, cast: _Cast) -> None:
        with engine.begin() as conn:
            _arm(conn, cast.schema, user_id=_OWNER)
            result = conn.execute(
                text("DELETE FROM audit_logs WHERE id = CAST(:id AS uuid)"),
                {"id": cast.rows["client_a_self"]},
            )
            assert result.rowcount == 0

        assert cast.rows["client_a_self"] in _visible(engine, cast.schema, _OWNER)

    def test_the_owner_cannot_write_a_row_in_someone_elses_name(
        self, engine: Engine, cast: _Cast
    ) -> None:
        with engine.connect() as conn:
            _arm(conn, cast.schema, user_id=_OWNER)
            with pytest.raises(DBAPIError, match="row-level security"):
                conn.execute(
                    text(
                        'INSERT INTO audit_logs (id, "timestamp", expires_at, user_id,'
                        " actor_type, action, resource_type, resource_id, patient_id)"
                        " VALUES (gen_random_uuid(), now(), now() + interval '1 day', :u,"
                        " 'clinician', 'patient_viewed', 'patient', 'x', CAST(:p AS uuid))"
                    ),
                    {"u": _COLLEAGUE, "p": cast.client_a},
                )
            conn.rollback()
