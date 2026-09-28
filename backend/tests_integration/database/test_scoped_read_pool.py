# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.
"""Nothing a one-statement scoped read arms survives it, even on a pooled
connection.

``read_once_as`` lets an unauthenticated path read one row-scoped statement
as one principal. Its safety rests on three things, pinned here as the
non-superuser, RLS-enforced role the app runs as:

1. Inside the transaction: the statement after the helper returns cannot
   read what the helper could. That is the savepoint rollback itself.
2. Across the pool: the physical connection handed to the next request
   carries no principal, no tenant ``search_path``, and cannot read the row
   — after the sign-in phone read (as the patient) and after the recovery
   lookup (as each clinician of the practice in turn).
3. The same pooled hand-off after an ordinary clinician-armed transaction,
   as a baseline for what "clean" means on this pool.

The engine has ``pool_size=1, max_overflow=0``, so there is one physical
connection and every checkout reuses it; ``pg_backend_pid()`` is asserted
equal across checkouts so a test cannot pass by getting a fresh one.

Each "request" runs in an empty ``contextvars.Context``. The engine's checkout
listener re-applies the tenant ``search_path`` from a request ContextVar, and
the next request on a pooled connection starts with none of the previous
request's ContextVars — so an empty context is what the next request sees.

Run: ``make test-integration``.
"""

from __future__ import annotations

import contextvars
import os
import uuid
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import Session

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

    from sqlalchemy.engine import Engine

_DB_URL = os.environ.get("DATABASE_URL", "")
pytestmark = pytest.mark.skipif(
    not _DB_URL or os.environ.get("DATABASE_BACKEND") != "postgres",
    reason=(
        "PostgreSQL not configured. Set DATABASE_URL and "
        "DATABASE_BACKEND=postgres; testcontainers should set both."
    ),
)

_SUFFIX = uuid.uuid4().hex[:8]
_SCHEMA = f"practice_test_scoped_{_SUFFIX}"
_PRACTICE_ID = f"practice-scoped-{_SUFFIX}"
_CLINICIAN = str(uuid.uuid4())
_PATIENT_A = str(uuid.uuid4())
_NEUTRAL_SEARCH_PATH = "platform, public"


def _in_a_new_request[T](fn: Callable[[], T]) -> T:
    """Run *fn* with every ContextVar at its default, as a new request would."""
    return contextvars.Context().run(fn)


@pytest.fixture(scope="module")
def setup_engine() -> Iterator[Engine]:
    backend_dir = Path(__file__).resolve().parents[2]
    cfg = Config(str(backend_dir / "alembic.ini"))
    cfg.set_main_option("script_location", str(backend_dir / "alembic"))
    command.upgrade(cfg, "head")
    eng = create_engine(_DB_URL, pool_pre_ping=True)
    yield eng
    eng.dispose()


@pytest.fixture(scope="module")
def practice(setup_engine: Engine) -> Iterator[str]:
    """A provisioned practice holding patient A, on the clinician's caseload.

    The clinician owns the practice in the platform schema, which is how the
    recovery lookup finds whom to read as.
    """
    from datetime import UTC, datetime  # noqa: PLC0415

    from app.db.platform_models import PlatformUserRow, PracticeRow  # noqa: PLC0415
    from app.db.provisioning import create_practice_schema  # noqa: PLC0415

    with setup_engine.connect() as conn:
        conn.execute(text("SET search_path = practice, platform, public"))
        conn.commit()
    create_practice_schema(setup_engine, _SCHEMA)
    now = datetime.now(UTC)
    with Session(bind=setup_engine) as session:
        session.add(
            PlatformUserRow(
                id=_CLINICIAN, email=f"{_PRACTICE_ID}@example.test", name="Owner", created_at=now
            )
        )
        session.flush()
        session.add(
            PracticeRow(
                id=_PRACTICE_ID,
                name="Scoped Read Practice",
                schema_name=_SCHEMA,
                owner_email=f"{_PRACTICE_ID}@example.test",
                owner_user_id=_CLINICIAN,
                created_at=now,
            )
        )
        session.commit()
    with setup_engine.begin() as conn:
        conn.execute(text(f"SET search_path = {_SCHEMA}, platform, public"))
        conn.execute(text("SELECT set_config('app.current_user_id', :u, true)"), {"u": _CLINICIAN})
        conn.execute(
            text(
                "INSERT INTO patients (id, first_name, last_name, first_name_lower, "
                "last_name_lower, status, session_count, email, phone, "
                "created_at, updated_at) "
                "VALUES (CAST(:p AS uuid), 'Ada', 'Tester', 'ada', 'tester', 'active', "
                "0, 'a@example.test', '+15005550006', now(), now())"
            ),
            {"p": _PATIENT_A},
        )
        conn.execute(
            text(
                "INSERT INTO patient_clinicians (patient_id, user_id, granted_by) "
                "VALUES (CAST(:p AS uuid), :u, :u)"
            ),
            {"p": _PATIENT_A, "u": _CLINICIAN},
        )
    yield _SCHEMA
    with setup_engine.begin() as conn:
        conn.execute(text(f'DROP SCHEMA IF EXISTS "{_SCHEMA}" CASCADE'))
        conn.execute(text("DELETE FROM platform.practices WHERE id = :i"), {"i": _PRACTICE_ID})
        conn.execute(
            text("DELETE FROM platform.users WHERE id = CAST(:i AS uuid)"), {"i": _CLINICIAN}
        )


@pytest.fixture
def pooled() -> Iterator[Engine]:
    """One physical connection, reused by every checkout."""
    eng = create_engine(_DB_URL, pool_size=1, max_overflow=0)
    yield eng
    eng.dispose()


def _patient_a_phone() -> Any:
    from app.db.models import PatientRow  # noqa: PLC0415

    return select(PatientRow.phone).where(PatientRow.id == _PATIENT_A)


def _count_a(conn_or_session: Any) -> int:
    return int(
        conn_or_session.execute(
            text(f"SELECT count(*) FROM {_SCHEMA}.patients WHERE id = CAST(:p AS uuid)"),  # noqa: S608
            {"p": _PATIENT_A},
        ).scalar_one()
    )


def _next_request_sees(pooled: Engine) -> dict[str, Any]:
    """What the next request finds on the pooled connection, before it arms anything."""

    def look() -> dict[str, Any]:
        with pooled.connect() as conn:
            seen = {
                "pid": conn.execute(text("SELECT pg_backend_pid()")).scalar_one(),
                "patient": conn.execute(
                    text("SELECT current_setting('app.current_patient_id', true)")
                ).scalar_one(),
                "user": conn.execute(
                    text("SELECT current_setting('app.current_user_id', true)")
                ).scalar_one(),
                "search_path": conn.execute(text("SHOW search_path")).scalar_one(),
            }
            # Then enter the tenant with no principal, as the next request's
            # own ``set_tenant_schema`` would, and try the row. Tried from the
            # neutral path instead, the policy's ``has_patient_access`` cannot
            # resolve ``patient_clinicians`` at all and the read errors — safe,
            # but it would not say whether the row is visible.
            conn.execute(text(f"SET search_path = {_SCHEMA}, platform, public"))
            seen["a_visible"] = _count_a(conn)
            conn.rollback()
            return seen

    return _in_a_new_request(look)


def _assert_clean(seen: dict[str, Any], first_pid: int) -> None:
    assert seen["pid"] == first_pid, "the pool handed out a different connection"
    assert not seen["patient"], f"app.current_patient_id leaked: {seen['patient']!r}"
    assert not seen["user"], f"app.current_user_id leaked: {seen['user']!r}"
    assert seen["search_path"] == _NEUTRAL_SEARCH_PATH, f"search_path: {seen['search_path']!r}"
    assert seen["a_visible"] == 0, "patient A's row is readable on the reused connection"


def test_a_scoped_read_leaves_nothing_on_the_pooled_connection(
    practice: str, pooled: Engine
) -> None:
    from app.db import read_once_as, set_tenant_schema  # noqa: PLC0415

    def request() -> int:
        with Session(bind=pooled) as session:
            set_tenant_schema(session, practice)
            pid = int(session.execute(text("SELECT pg_backend_pid()")).scalar_one())
            rows = read_once_as(
                session,
                principal="app.current_patient_id",
                value=_PATIENT_A,
                statement=_patient_a_phone(),
            )
            assert [row.phone for row in rows] == ["+15005550006"], "the helper read nothing"
            session.commit()
            return pid

    first_pid = _in_a_new_request(request)
    _assert_clean(_next_request_sees(pooled), first_pid)


def test_the_recovery_lookup_leaves_nothing_on_the_pooled_connection(
    practice: str, pooled: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The real ``find_patient``, reading as each clinician of the practice
    in turn, on the one pooled connection."""
    from app.db import set_tenant_schema  # noqa: PLC0415
    from app.portal import recovery_gateway  # noqa: PLC0415

    def pooled_session(schema: str | None = None) -> Session:
        session = Session(bind=pooled)
        if schema:
            set_tenant_schema(session, schema)
        return session

    # The gateway opens its own standalone session; point it at the pool
    # under test rather than the app's engine.
    monkeypatch.setattr(recovery_gateway, "create_standalone_session", pooled_session)

    def request() -> int:
        with recovery_gateway.DbRecoveryGateway().open(practice, None) as work:
            target = work.find_patient("a@example.test")
            assert target is not None, "the lookup found nothing to prove a leak against"
            assert target.patient_id == _PATIENT_A
            from app.portal.db_store import DbPortalAuthStore  # noqa: PLC0415

            assert isinstance(work.challenges, DbPortalAuthStore)
            session = work.challenges._session
            return int(session.execute(text("SELECT pg_backend_pid()")).scalar_one())

    first_pid = _in_a_new_request(request)
    _assert_clean(_next_request_sees(pooled), first_pid)


def test_a_clinician_armed_transaction_leaves_nothing_on_the_pooled_connection(
    practice: str, pooled: Engine
) -> None:
    from app.db import arm_current_user_id, set_tenant_schema  # noqa: PLC0415

    def request() -> int:
        with Session(bind=pooled) as session:
            set_tenant_schema(session, practice)
            arm_current_user_id(session, _CLINICIAN)
            pid = int(session.execute(text("SELECT pg_backend_pid()")).scalar_one())
            assert _count_a(session) == 1, "the armed clinician could not read their patient"
            session.commit()
            return pid

    first_pid = _in_a_new_request(request)
    _assert_clean(_next_request_sees(pooled), first_pid)


def test_the_statement_after_a_scoped_read_cannot_read_the_row(
    practice: str, pooled: Engine
) -> None:
    """The savepoint rollback itself: same session, same transaction."""
    from app.db import read_once_as, set_tenant_schema  # noqa: PLC0415

    def request() -> None:
        with Session(bind=pooled) as session:
            set_tenant_schema(session, practice)
            rows = read_once_as(
                session,
                principal="app.current_patient_id",
                value=_PATIENT_A,
                statement=_patient_a_phone(),
            )
            assert len(rows) == 1
            assert session.in_transaction(), "the check must run in the same transaction"
            assert not session.execute(
                text("SELECT current_setting('app.current_patient_id', true)")
            ).scalar_one()
            assert _count_a(session) == 0
            session.rollback()

    _in_a_new_request(request)
