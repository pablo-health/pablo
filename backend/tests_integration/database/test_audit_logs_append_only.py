# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""audit_logs is append-only (PABLO-5zy).

Companion integration test for the ``audit_logs_append_only`` BEFORE
trigger installed into every tenant schema (migration ``b33a493310b6``;
baked into ``tenant_template.sql``).

The app connects as the table OWNER under ``FORCE ROW LEVEL SECURITY`` and
the retention cron shares that role, so table privileges can't enforce
append-only (owners bypass them) without also breaking the legitimate
purge. The trigger fires even for the owner and is the actual enforcement
point. This test locks in the contract:

  * UPDATE on an audit_logs row is blocked (append-only).
  * DELETE on an audit_logs row is blocked (append-only).
  * TRUNCATE of audit_logs is blocked (append-only) — a row-level UPDATE/DELETE
    trigger does NOT fire on TRUNCATE, so a separate statement-level
    ``audit_logs_no_truncate`` BEFORE TRUNCATE trigger (migration
    ``a7e3f1b9c204``) closes the wholesale-wipe gap.
  * DELETE / TRUNCATE wrapped with ``SET LOCAL app.allow_audit_purge = 'on'`` —
    the retention-cron / authorized-purge path — succeeds.

Uses the alembic-upgrade-head + ``create_practice_schema`` fixture pattern
(NOT ORM ``create_all``) because the trigger is raw DDL that only lands via
the regenerated template / migration chain. The testcontainers conftest
runs as the non-superuser, ``NOBYPASSRLS`` ``pablo`` role — the same posture
as prod, so the trigger must fire for it.
"""

from __future__ import annotations

import os
import uuid
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError, InternalError, ProgrammingError

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
    from app.db.provisioning import create_practice_schema  # noqa: PLC0415

    # Warm the pool so policy CREATEs that reference ``has_patient_access``
    # (which lives in ``practice``) resolve during provisioning.
    with engine.connect() as conn:
        conn.execute(text("SET search_path = practice, platform, public"))
        conn.commit()

    schema = f"practice_test_append_only_{uuid.uuid4().hex[:8]}"
    create_practice_schema(engine, schema)
    yield schema
    with engine.connect() as conn:
        conn.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        conn.commit()


def _seed_audit_row(engine: Engine, schema: str, user_id: str) -> str:
    """Insert one audit_logs row; return its id.

    audit_logs is RLS-protected by a ``user_id`` policy, so the INSERT
    needs ``app.current_user_id`` armed (WITH CHECK) — done in the same
    transaction as the INSERT.
    """
    row_id = str(uuid.uuid4())
    with engine.begin() as conn:
        conn.execute(text(f"SET search_path = {schema}, platform, public"))
        conn.execute(
            text("SELECT set_config('app.current_user_id', :u, false)"),
            {"u": user_id},
        )
        conn.execute(
            text(
                'INSERT INTO audit_logs (id, "timestamp", expires_at, '
                "user_id, action, resource_type, resource_id) "
                "VALUES (CAST(:id AS uuid), now(), now() - interval '1 day', "
                ":u, 'read', 'patient', 'res-1')"
            ),
            {"id": row_id, "u": user_id},
        )
    return row_id


class TestAuditLogsAppendOnly:
    """The append-only trigger blocks mutation; only the GUC-armed purge wins."""

    def test_update_is_blocked(self, engine: Engine, tenant_schema: str) -> None:
        user_id = "append-only-update"
        row_id = _seed_audit_row(engine, tenant_schema, user_id)

        with engine.connect() as conn:
            conn.execute(text(f"SET search_path = {tenant_schema}, platform, public"))
            conn.execute(
                text("SELECT set_config('app.current_user_id', :u, false)"),
                {"u": user_id},
            )
            with pytest.raises((IntegrityError, InternalError, ProgrammingError)) as exc:
                conn.execute(
                    text("UPDATE audit_logs SET action = 'tamper' WHERE id = CAST(:id AS uuid)"),
                    {"id": row_id},
                )
            conn.rollback()
        assert "append-only" in str(exc.value)

    def test_delete_is_blocked(self, engine: Engine, tenant_schema: str) -> None:
        user_id = "append-only-delete"
        row_id = _seed_audit_row(engine, tenant_schema, user_id)

        with engine.connect() as conn:
            conn.execute(text(f"SET search_path = {tenant_schema}, platform, public"))
            conn.execute(
                text("SELECT set_config('app.current_user_id', :u, false)"),
                {"u": user_id},
            )
            with pytest.raises((IntegrityError, InternalError, ProgrammingError)) as exc:
                conn.execute(
                    text("DELETE FROM audit_logs WHERE id = CAST(:id AS uuid)"),
                    {"id": row_id},
                )
            conn.rollback()
        assert "append-only" in str(exc.value)

    def test_retention_path_delete_succeeds(self, engine: Engine, tenant_schema: str) -> None:
        user_id = "append-only-purge"
        row_id = _seed_audit_row(engine, tenant_schema, user_id)

        # The retention cron's exact pattern: SET LOCAL the purge GUC in the
        # same transaction as the DELETE.
        with engine.begin() as conn:
            conn.execute(text(f"SET search_path = {tenant_schema}, platform, public"))
            conn.execute(
                text("SELECT set_config('app.current_user_id', :u, false)"),
                {"u": user_id},
            )
            conn.execute(text("SET LOCAL app.allow_audit_purge = 'on'"))
            result = conn.execute(
                text("DELETE FROM audit_logs WHERE id = CAST(:id AS uuid)"),
                {"id": row_id},
            )
        assert result.rowcount == 1

    def test_truncate_is_blocked(self, engine: Engine, tenant_schema: str) -> None:
        # A row-level UPDATE/DELETE trigger doesn't fire on TRUNCATE; the
        # separate BEFORE TRUNCATE trigger must, so the app role can't wipe the
        # whole trail in one statement.
        _seed_audit_row(engine, tenant_schema, "append-only-truncate")

        with engine.connect() as conn:
            conn.execute(text(f"SET search_path = {tenant_schema}, platform, public"))
            with pytest.raises((IntegrityError, InternalError, ProgrammingError)) as exc:
                conn.execute(text("TRUNCATE TABLE audit_logs"))
            conn.rollback()
        assert "append-only" in str(exc.value)

    def test_truncate_with_purge_guc_succeeds(self, engine: Engine, tenant_schema: str) -> None:
        _seed_audit_row(engine, tenant_schema, "append-only-truncate-purge")

        with engine.begin() as conn:
            conn.execute(text(f"SET search_path = {tenant_schema}, platform, public"))
            conn.execute(text("SET LOCAL app.allow_audit_purge = 'on'"))
            conn.execute(text("TRUNCATE TABLE audit_logs"))
            remaining = conn.execute(text("SELECT count(*) FROM audit_logs")).scalar()
        assert remaining == 0
