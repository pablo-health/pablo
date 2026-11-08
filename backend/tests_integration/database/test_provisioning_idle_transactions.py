# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Practice provisioning under a short ``idle_in_transaction_session_timeout``.

A practice build runs for seconds, and on a loaded database for minutes. Any
connection that sits inside an open transaction for that long is terminated
by the server once ``idle_in_transaction_session_timeout`` passes, so nothing
in the provisioning path may wait on the build from inside a transaction.

Each test here runs against an engine whose connections carry a 2 s timeout
and makes the build take longer than that by slowing one of its steps. All
three failed before the fix:

* the pentest provision held its request transaction open across the build,
  so the audit write and the commit after it hit a terminated connection;
* ``create_practice_schema`` held its advisory lock from inside an open
  transaction, so the server terminated that connection and the lock went
  with it, mid-build;
* the pentest drop did not wait for a build in flight, so it removed the
  schema from under the build's next statement.
"""

from __future__ import annotations

import os
import threading
import time
import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any
from unittest import mock

import pytest
from app.db import provisioning
from app.db.platform_models import PlatformAuditLogRow, PracticeRow
from app.models.platform_audit import PlatformAuditAction
from app.repositories.postgres.platform_audit import PostgresPlatformAuditRepository
from app.services import pentest_tenant_service
from app.services.pentest_tenant_service import PentestTenantService
from app.services.platform_audit_service import PlatformAuditService
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import Session

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

    from sqlalchemy.engine import Engine

_db_url = os.environ.get("DATABASE_URL", "")
pytestmark = pytest.mark.skipif(
    not _db_url or os.environ.get("DATABASE_BACKEND") != "postgres",
    reason=(
        "PostgreSQL not configured. Set DATABASE_URL and "
        "DATABASE_BACKEND=postgres; testcontainers should set both."
    ),
)

#: The server terminates a connection idle inside a transaction this long.
IDLE_TIMEOUT_MS = 2000
#: How long the slowed build step pauses: comfortably past the timeout.
SLOW_STEP_SECONDS = 4.0


@pytest.fixture
def short_timeout_engine(engine: Engine) -> Iterator[Engine]:
    """Connections carry a short idle-in-transaction timeout, and the short
    ``lock_timeout`` the app also sets, so a lock wait that does not widen it
    fails here as it would in a deployment."""
    del engine  # depended on for the migrated database, not used directly
    eng = create_engine(
        _db_url,
        pool_pre_ping=True,
        connect_args={
            "options": (
                f"-c idle_in_transaction_session_timeout={IDLE_TIMEOUT_MS} -c lock_timeout=1000"
            )
        },
    )
    yield eng
    eng.dispose()


class _SlowedStamp:
    """``_stamp_alembic_at_head``, after a pause: the template is applied, the
    build is not finished, and the pause outlasts the idle timeout. Records
    when the pause began and when the stamp itself completed."""

    def __init__(self) -> None:
        self.started = threading.Event()
        self.stamped_at = 0.0
        self._real = provisioning._stamp_alembic_at_head

    def __call__(self, engine: Engine, schema_name: str) -> None:
        self.started.set()
        time.sleep(SLOW_STEP_SECONDS)
        self._real(engine, schema_name)
        self.stamped_at = time.monotonic()


def _schema_exists(engine: Engine, schema_name: str) -> bool:
    with engine.connect() as conn:
        return (
            conn.execute(
                text("SELECT 1 FROM information_schema.schemata WHERE schema_name = :s"),
                {"s": schema_name},
            ).first()
            is not None
        )


def _drop(engine: Engine, schema_name: str) -> None:
    with engine.connect() as conn:
        conn.execute(text(f'DROP SCHEMA IF EXISTS "{schema_name}" CASCADE'))
        conn.commit()


def _service(session: Session) -> PentestTenantService:
    return PentestTenantService(
        session=session, audit=PlatformAuditService(PostgresPlatformAuditRepository(session))
    )


def _new_schema_name() -> str:
    return f"practice_pentest_{uuid.uuid4().hex}"


class _Thread(threading.Thread):
    """A thread that keeps what its target raised and when it finished."""

    def __init__(self, target: Callable[[], Any]) -> None:
        super().__init__(daemon=True)
        self._fn = target
        self.error: BaseException | None = None
        self.finished_at = 0.0

    def run(self) -> None:
        try:
            self._fn()
        except BaseException as exc:  # reported by the test
            self.error = exc
        finally:
            self.finished_at = time.monotonic()


def test_pentest_provision_survives_a_build_longer_than_the_idle_timeout(
    short_timeout_engine: Engine,
) -> None:
    email = f"pentestuser-{uuid.uuid4().hex[:8]}@pablo.health"
    with (
        mock.patch.object(pentest_tenant_service, "get_engine", return_value=short_timeout_engine),
        mock.patch.object(provisioning, "_stamp_alembic_at_head", _SlowedStamp()),
        Session(short_timeout_engine) as session,
    ):
        handle = _service(session).provision(actor_user_id="test-runner", user_emails=[email])
        session.commit()

    try:
        with Session(short_timeout_engine) as session:
            assert session.get(PracticeRow, handle.tenant_id) is not None
            audit = session.execute(
                select(PlatformAuditLogRow).where(
                    PlatformAuditLogRow.tenant_schema == handle.schema_name,
                    PlatformAuditLogRow.action == PlatformAuditAction.PENTEST_TENANT_PROVISIONED,
                )
            ).first()
            assert audit is not None, "the provision's audit row was not written"
        assert _schema_exists(short_timeout_engine, handle.schema_name)
    finally:
        with (
            mock.patch.object(
                pentest_tenant_service, "get_engine", return_value=short_timeout_engine
            ),
            Session(short_timeout_engine) as session,
        ):
            _service(session).deprovision(tenant_id=handle.tenant_id, actor_user_id="test-runner")
            session.commit()


def test_the_build_lock_is_held_for_the_whole_of_a_slow_build(
    short_timeout_engine: Engine,
) -> None:
    schema_name = _new_schema_name()
    slowed = _SlowedStamp()
    try:
        with mock.patch.object(provisioning, "_stamp_alembic_at_head", slowed):
            first = _Thread(
                lambda: provisioning.create_practice_schema(short_timeout_engine, schema_name)
            )
            first.start()
            assert slowed.started.wait(60)
            # Past the idle timeout, while the build is still paused.
            time.sleep(IDLE_TIMEOUT_MS / 1000 + 1)
            assert first.is_alive()

            with short_timeout_engine.connect() as probe:
                got_it = probe.execute(
                    text("SELECT pg_try_advisory_lock(hashtext(:s))"), {"s": schema_name}
                ).scalar()
                if got_it:
                    probe.execute(
                        text("SELECT pg_advisory_unlock(hashtext(:s))"), {"s": schema_name}
                    )
                probe.commit()
            assert not got_it, "the build lock was lost while the build was still running"

            # A second caller for the same schema waits for the first, then
            # finds it built. Its own lock wait is not under test, so it runs
            # without the short lock_timeout.
            second_engine = create_engine(_db_url, pool_pre_ping=True)
            try:
                second = _Thread(
                    lambda: provisioning.create_practice_schema(second_engine, schema_name)
                )
                second.start()
                first.join(60)
                second.join(60)
            finally:
                second_engine.dispose()

        assert first.error is None, f"first build failed: {first.error!r}"
        assert second.error is None, f"second build failed: {second.error!r}"
        assert slowed.stamped_at > 0
        assert second.finished_at >= slowed.stamped_at, "the second build did not wait"
    finally:
        _drop(short_timeout_engine, schema_name)


def test_pentest_deprovision_waits_for_a_build_in_flight(short_timeout_engine: Engine) -> None:
    schema_name = _new_schema_name()
    tenant_id = f"pentest-{uuid.uuid4().hex[:8]}"
    with Session(short_timeout_engine) as session:
        session.add(
            PracticeRow(
                id=tenant_id,
                name="Pentest Tenant mid-build",
                schema_name=schema_name,
                tenant_id=tenant_id,
                owner_email="",
                owner_user_id=None,
                product="pablo",
                status="active",
                is_active=True,
                is_pentest=True,
                created_at=datetime.now(UTC),
            )
        )
        session.commit()

    slowed = _SlowedStamp()
    try:
        with (
            mock.patch.object(
                pentest_tenant_service, "get_engine", return_value=short_timeout_engine
            ),
            mock.patch.object(provisioning, "_stamp_alembic_at_head", slowed),
        ):
            build = _Thread(
                lambda: provisioning.create_practice_schema(short_timeout_engine, schema_name)
            )
            build.start()
            assert slowed.started.wait(60)

            with Session(short_timeout_engine) as session:
                _service(session).deprovision(tenant_id=tenant_id, actor_user_id="test-runner")
                session.commit()
            deprovisioned_at = time.monotonic()
            build.join(60)

        assert build.error is None, f"the build failed under the drop: {build.error!r}"
        assert slowed.stamped_at > 0
        assert deprovisioned_at >= slowed.stamped_at, "the drop did not wait for the build"
        assert not _schema_exists(short_timeout_engine, schema_name)
        with Session(short_timeout_engine) as session:
            assert session.get(PracticeRow, tenant_id) is None
    finally:
        _drop(short_timeout_engine, schema_name)
        with Session(short_timeout_engine) as session:
            row = session.get(PracticeRow, tenant_id)
            if row is not None:
                session.delete(row)
                session.commit()
