# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""The retention purge has to survive RLS, not just the append-only trigger.

``audit_logs`` is protected twice over, and the two protections are
easy to confuse:

  * the ``audit_logs_append_only`` BEFORE trigger, which refuses every
    UPDATE/DELETE unless ``app.allow_audit_purge`` is armed; and
  * the row policy, which decides which rows a statement can *see* at
    all.

``test_audit_logs_append_only`` proves the first one lets the purge
through. Nothing proved the second one does, and the existing test
cannot: it arms ``app.current_user_id`` to the seeded row's own
``user_id`` before deleting, so the policy's ``USING`` clause matches
by construction. :func:`app.jobs.audit_retention_cron._delete_expired`
arms no such thing — it sets ``search_path``, arms the purge GUC, and
issues the DELETE. Under ``FORCE ROW LEVEL SECURITY`` and the
NOBYPASSRLS app role, ``current_setting('app.current_user_id', true)``
is NULL there, so ``user_id::text = NULL`` is NULL for every row and
the statement matches none of them.

That failure is silent in the worst way available: no error, no
refusal, ``rowcount`` 0, and a cron that logs a successful run having
purged nothing. Rows outlive their ``expires_at`` indefinitely, which
is a § 164.316(b)(2)(i) retention *ceiling* problem rather than a
floor one — the table keeps PHI-adjacent access records forever.

So these tests call the job's own functions rather than reimplementing
its SQL. A test that re-types the DELETE would pass while production
deleted nothing, which is exactly the gap being closed.

Uses the alembic-upgrade-head + ``create_practice_schema`` fixture
pattern (NOT ORM ``create_all``): the policy and the triggers are raw
DDL that only lands via the migration chain and the tenant template,
and per-tenant schemas are the ones that get RLS at all.
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
from sqlalchemy import create_engine, text

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

_NOW = datetime(2026, 8, 30, 12, 0, tzinfo=UTC)


@pytest.fixture(scope="module")
def engine() -> Iterator[Engine]:
    backend_dir = Path(__file__).resolve().parents[2]
    cfg = Config(str(backend_dir / "alembic.ini"))
    cfg.set_main_option("script_location", str(backend_dir / "alembic"))
    command.upgrade(cfg, "head")
    eng = create_engine(_db_url, pool_pre_ping=True)
    yield eng
    eng.dispose()


def _make_schema(engine: Engine, engine_label: str) -> str:
    from app.db.provisioning import create_practice_schema  # noqa: PLC0415

    with engine.connect() as conn:
        conn.execute(text("SET search_path = practice, platform, public"))
        conn.commit()

    schema = f"practice_test_purge_{engine_label}_{uuid.uuid4().hex[:8]}"
    create_practice_schema(engine, schema)
    return schema


@pytest.fixture
def tenant_schema(engine: Engine) -> Iterator[str]:
    """Function-scoped: each test purges, so it needs its own rows."""
    schema = _make_schema(engine, "a")
    yield schema
    with engine.connect() as conn:
        conn.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        conn.commit()


@pytest.fixture
def other_tenant_schema(engine: Engine) -> Iterator[str]:
    schema = _make_schema(engine, "b")
    yield schema
    with engine.connect() as conn:
        conn.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        conn.commit()


def _seed(
    engine: Engine,
    schema: str,
    *,
    actor_type: str,
    expires_at: datetime,
    actor_id: str | None = None,
) -> str:
    """Write one audit row through the policy, as its actor would.

    A row is inserted under whichever GUC its ``actor_type`` requires,
    because that is the only way the WITH CHECK arm passes — which is
    the point of the policy being split. The purge is then asked to
    reach rows it did not write and does not have an identity for.
    """
    row_id = str(uuid.uuid4())
    actor = actor_id or str(uuid.uuid4())
    guc = "app.current_patient_id" if actor_type == "patient" else "app.current_user_id"
    patient_id = actor if actor_type == "patient" else None

    with engine.begin() as conn:
        conn.execute(text(f"SET search_path = {schema}, platform, public"))
        conn.execute(text(f"SELECT set_config('{guc}', :a, true)"), {"a": actor})
        conn.execute(
            text(
                'INSERT INTO audit_logs (id, "timestamp", expires_at, user_id, '
                "actor_type, action, resource_type, resource_id, patient_id) "
                "VALUES (CAST(:id AS uuid), :ts, :exp, :user_id, :actor_type, "
                "'read', 'patient', 'res-1', CAST(:patient_id AS uuid))"
            ),
            {
                "id": row_id,
                "ts": expires_at - timedelta(days=2555),
                "exp": expires_at,
                "user_id": actor,
                "actor_type": actor_type,
                "patient_id": patient_id,
            },
        )
    return row_id


def _rows_remaining(engine: Engine, schema: str) -> int:
    """Count without a policy in the way.

    Read as the schema owner with RLS momentarily un-forced: the whole
    question here is what the purge left behind, and asking that
    question through the same policy that hid the rows from the purge
    would answer it wrong. Restored before the connection closes.
    """
    with engine.begin() as conn:
        conn.execute(text(f"ALTER TABLE {schema}.audit_logs NO FORCE ROW LEVEL SECURITY"))
        count = conn.execute(
            text(f"SELECT count(*) FROM {schema}.audit_logs")  # noqa: S608
        ).scalar_one()
        conn.execute(text(f"ALTER TABLE {schema}.audit_logs FORCE ROW LEVEL SECURITY"))
    return int(count)


class TestTheCronCanReachExpiredRows:
    """The red ones. These fail against the policy as it stands."""

    def test_the_purge_deletes_an_expired_clinician_row(
        self, engine: Engine, tenant_schema: str
    ) -> None:
        """``_delete_expired`` arms no identity GUC, so the policy's
        ``USING`` clause matches nothing and the DELETE is a silent
        no-op. The job reports success having purged zero rows."""
        from app.jobs.audit_retention_cron import _delete_expired  # noqa: PLC0415

        _seed(
            engine,
            tenant_schema,
            actor_type="clinician",
            expires_at=_NOW - timedelta(days=1),
        )

        deleted = _delete_expired(engine, tenant_schema, _NOW)

        assert deleted == 1
        assert _rows_remaining(engine, tenant_schema) == 0

    def test_the_purge_deletes_an_expired_patient_actor_row(
        self, engine: Engine, tenant_schema: str
    ) -> None:
        """The patient half is strictly worse than the clinician one.

        A clinician row is at least reachable by a connection that
        arms that clinician's id. Since the read narrowing, a
        patient-actor row is visible to no principal at all — so
        there is no identity the purge could arm that would reach it.
        These rows would outlive their retention window permanently.
        """
        from app.jobs.audit_retention_cron import _delete_expired  # noqa: PLC0415

        _seed(
            engine,
            tenant_schema,
            actor_type="patient",
            expires_at=_NOW - timedelta(days=1),
        )

        deleted = _delete_expired(engine, tenant_schema, _NOW)

        assert deleted == 1
        assert _rows_remaining(engine, tenant_schema) == 0

    @pytest.mark.parametrize("actor_type", ["anonymous", "system", "platform_staff"])
    def test_the_purge_reaches_every_other_actor_kind(
        self, engine: Engine, tenant_schema: str, actor_type: str
    ) -> None:
        """A public-booking row and a cron's own row expire like any other."""
        from app.jobs.audit_retention_cron import _delete_expired  # noqa: PLC0415

        _seed(
            engine,
            tenant_schema,
            actor_type=actor_type,
            expires_at=_NOW - timedelta(days=1),
        )

        assert _delete_expired(engine, tenant_schema, _NOW) == 1

    def test_the_dry_run_count_sees_what_the_purge_would_delete(
        self, engine: Engine, tenant_schema: str
    ) -> None:
        """``--dry-run`` reads through the same policy the DELETE does.

        Worth its own test because the two failures compound: an
        operator checking whether the purge has work to do is told
        "none", which corroborates the empty purge rather than
        contradicting it.
        """
        from app.jobs.audit_retention_cron import _count_expired  # noqa: PLC0415

        _seed(
            engine,
            tenant_schema,
            actor_type="clinician",
            expires_at=_NOW - timedelta(days=1),
        )
        _seed(
            engine,
            tenant_schema,
            actor_type="patient",
            expires_at=_NOW - timedelta(days=1),
        )

        assert _count_expired(engine, tenant_schema, _NOW) == 2


class TestThePurgeStaysWithinItsBounds:
    """Reaching expired rows must not turn into reaching every row."""

    def test_unexpired_rows_survive(self, engine: Engine, tenant_schema: str) -> None:
        from app.jobs.audit_retention_cron import _delete_expired  # noqa: PLC0415

        _seed(
            engine,
            tenant_schema,
            actor_type="clinician",
            expires_at=_NOW + timedelta(days=1),
        )
        _seed(
            engine,
            tenant_schema,
            actor_type="patient",
            expires_at=_NOW + timedelta(days=1),
        )

        assert _delete_expired(engine, tenant_schema, _NOW) == 0
        assert _rows_remaining(engine, tenant_schema) == 2

    def test_purging_one_tenant_leaves_another_untouched(
        self, engine: Engine, tenant_schema: str, other_tenant_schema: str
    ) -> None:
        """The job fans across schemas one at a time; whatever makes the
        DELETE able to see rows must not make it able to see a
        neighbour's."""
        from app.jobs.audit_retention_cron import _delete_expired  # noqa: PLC0415

        for schema in (tenant_schema, other_tenant_schema):
            _seed(
                engine,
                schema,
                actor_type="clinician",
                expires_at=_NOW - timedelta(days=1),
            )

        assert _delete_expired(engine, tenant_schema, _NOW) == 1
        assert _rows_remaining(engine, other_tenant_schema) == 1

    def test_the_append_only_trigger_still_refuses_an_unarmed_delete(
        self, engine: Engine, tenant_schema: str
    ) -> None:
        """Whatever lets the purge see rows must not double as a way to
        delete them without the purge GUC. The trigger is the other
        half of the contract and has to keep firing."""
        from sqlalchemy.exc import (  # noqa: PLC0415
            IntegrityError,
            InternalError,
            ProgrammingError,
        )

        clinician_id = str(uuid.uuid4())
        _seed(
            engine,
            tenant_schema,
            actor_type="clinician",
            expires_at=_NOW - timedelta(days=1),
            actor_id=clinician_id,
        )

        with engine.connect() as conn:
            conn.execute(text(f"SET search_path = {tenant_schema}, platform, public"))
            conn.execute(
                text("SELECT set_config('app.current_user_id', :u, false)"),
                {"u": clinician_id},
            )
            with pytest.raises((IntegrityError, InternalError, ProgrammingError)) as exc:
                conn.execute(
                    text("DELETE FROM audit_logs WHERE expires_at < :as_of"), {"as_of": _NOW}
                )
            conn.rollback()

        assert "append-only" in str(exc.value)
