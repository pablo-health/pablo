# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Real-Postgres proof for payer enrollments: provisioning and the reminder path.

A fresh tenant provisioned from the template must carry ``payer_enrollments``
and the billing profile's two new columns, with ``payer_enrollments`` left
un-policied like ``payers`` (its boundary is the tenant schema).

The lifecycle then runs the way the daily job runs it — a tenant session,
the clearinghouse answered from recorded fixtures, the session armed as
each request's owner — and proves the one thing SQLite cannot: a status
change to ``provider_action_required`` raised from a worker lands as a
compliance reminder under that clinician's row policy, visible to them and
to nobody else, with a real NOSUPERUSER NOBYPASSRLS role (see conftest.py).
Non-vacuous: "B sees nothing" follows "A sees the row".

Run: ``make test-integration``.
"""

from __future__ import annotations

import base64
import os
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

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

_CLINICIAN_A = "1a3d0a8e-6f0c-5f7e-9c6b-6c2f5a1c1a01"
_CLINICIAN_B = "2b4e1b9f-7a1d-5a8f-8d7c-7d3a6b2d2b02"

_PROFILE = {
    "legal_name": "Pablo Health Test Provider",
    "tax_id": "84-4459714",
    "tax_id_type": "ein",
    "billing_npi": "1999999984",
    "address_line1": "1 Test St",
    "city": "Atlanta",
    "state": "GA",
    "postal_code": "30301",
    "phone": "4045550100",
    "contact_email": "billing@example.com",
}


@pytest.fixture(scope="module", autouse=True)
def _encryption_key() -> Iterator[None]:
    """The billing profile's tax id is encrypted with the calendar-token key."""
    from app.settings import get_settings  # noqa: PLC0415

    previous = os.environ.get("GOOGLE_CALENDAR_ENCRYPTION_KEY")
    os.environ["GOOGLE_CALENDAR_ENCRYPTION_KEY"] = base64.b64encode(os.urandom(32)).decode()
    get_settings.cache_clear()
    yield
    if previous is None:
        del os.environ["GOOGLE_CALENDAR_ENCRYPTION_KEY"]
    else:
        os.environ["GOOGLE_CALENDAR_ENCRYPTION_KEY"] = previous
    get_settings.cache_clear()


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

    with engine.connect() as conn:
        conn.execute(text("SET search_path = practice, platform, public"))
        conn.commit()

    schema = f"practice_test_enroll_{uuid.uuid4().hex[:8]}"
    create_practice_schema(engine, schema)
    yield schema
    with engine.connect() as conn:
        conn.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        conn.commit()


class _TenantSession:
    """A tenant session armed as one clinician, the way a worker opens one."""

    def __init__(self, engine: Engine, schema: str, user_id: str) -> None:
        from app.db import (  # noqa: PLC0415
            _current_tenant_schema,
            _current_user_id,
            arm_current_user_id,
        )
        from sqlalchemy.orm import Session as OrmSession  # noqa: PLC0415

        self._schema_token = _current_tenant_schema.set(schema)
        self._uid_token = _current_user_id.set(user_id)
        self.session = OrmSession(bind=engine)
        self.session.execute(text(f"SET search_path = {schema}, platform, public"))
        arm_current_user_id(self.session, user_id)

    def close(self) -> None:
        from app.db import _current_tenant_schema, _current_user_id  # noqa: PLC0415

        self.session.close()
        _current_tenant_schema.reset(self._schema_token)
        _current_user_id.reset(self._uid_token)


def _reminders_visible_to(engine: Engine, schema: str, user_id: str) -> list[Any]:
    from app.db.models import ComplianceItemRow  # noqa: PLC0415
    from sqlalchemy import select  # noqa: PLC0415

    scoped = _TenantSession(engine, schema, user_id)
    try:
        return list(scoped.session.execute(select(ComplianceItemRow)).scalars().all())
    finally:
        scoped.close()


class TestProvisioning:
    def test_fresh_tenant_has_the_table_and_the_profile_columns(
        self, engine: Engine, tenant_schema: str
    ) -> None:
        with engine.connect() as conn:
            tables = conn.execute(
                text(
                    "SELECT table_name FROM information_schema.tables "
                    "WHERE table_schema = :s AND table_name = 'payer_enrollments'"
                ),
                {"s": tenant_schema},
            ).scalars()
            assert list(tables) == ["payer_enrollments"]
            columns = conn.execute(
                text(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_schema = :s AND table_name = 'practice_billing_profile' "
                    "AND column_name IN ('contact_email', 'clearinghouse_provider_id')"
                ),
                {"s": tenant_schema},
            ).scalars()
            assert set(columns) == {"contact_email", "clearinghouse_provider_id"}

    def test_rls_posture(self, engine: Engine, tenant_schema: str) -> None:
        """payer_enrollments is practice-level like payers: no row policy."""
        with engine.connect() as conn:
            rows = conn.execute(
                text(
                    "SELECT c.relname, c.relrowsecurity, c.relforcerowsecurity "
                    "FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
                    "WHERE n.nspname = :s AND c.relname IN ('payers', 'payer_enrollments')"
                ),
                {"s": tenant_schema},
            ).all()
            posture = {name: (rls, forced) for name, rls, forced in rows}
            assert posture["payer_enrollments"] == (False, False)
            assert posture["payers"] == (False, False)


class TestWorkerRaisedReminder:
    def test_action_required_lands_as_the_owners_reminder_and_resolves(
        self, engine: Engine, tenant_schema: str
    ) -> None:
        from app.claims.enrollment import refresh_enrollments, request_enrollments  # noqa: PLC0415
        from app.db.models import PayerEnrollmentRow, PayerRow  # noqa: PLC0415
        from app.services.practice_billing_profile import update_billing_profile  # noqa: PLC0415
        from sqlalchemy import select  # noqa: PLC0415
        from tests.enrollment_fakes import (  # noqa: PLC0415
            INSTRUCTIONS,
            TEST_PAYER_ID,
            FakeClearinghouse,
            enrollment_fixture,
        )

        client = FakeClearinghouse()
        now = datetime.now(UTC)
        payer_row_id = str(uuid.uuid4())

        # Clinician A puts the payer on file and asks to enroll.
        scoped = _TenantSession(engine, tenant_schema, _CLINICIAN_A)
        try:
            update_billing_profile(scoped.session, dict(_PROFILE))
            scoped.session.add(
                PayerRow(
                    id=payer_row_id,
                    name="Stedi Test Payer",
                    payer_id=TEST_PAYER_ID,
                    created_at=now,
                    updated_at=now,
                )
            )
            scoped.session.flush()
            [row] = request_enrollments(
                scoped.session, client, payer_row_id=payer_row_id, user_id=_CLINICIAN_A
            )
            vendor_request_id = row.vendor_request_id
            scoped.session.commit()
        finally:
            scoped.close()

        # The daily job's pass, with the payer now wanting paperwork. The
        # session is opened as B on purpose: the refresh must re-arm as the
        # request's owner (A) for the reminder insert to pass A's row policy.
        client.listing = [
            enrollment_fixture(vendor_id=vendor_request_id, status="PROVIDER_ACTION_REQUIRED")
        ]
        scoped = _TenantSession(engine, tenant_schema, _CLINICIAN_B)
        try:
            assert refresh_enrollments(scoped.session, client) == 1
            scoped.session.commit()
        finally:
            scoped.close()

        [reminder] = _reminders_visible_to(engine, tenant_schema, _CLINICIAN_A)
        assert reminder.item_type == "claim_enrollment_action_required"
        assert INSTRUCTIONS in (reminder.notes or "")
        assert reminder.completed_at is None
        assert _reminders_visible_to(engine, tenant_schema, _CLINICIAN_B) == []

        # The payer answers; the reminder is done and the payer is active.
        client.listing = [enrollment_fixture(vendor_id=vendor_request_id, status="LIVE")]
        scoped = _TenantSession(engine, tenant_schema, _CLINICIAN_B)
        try:
            assert refresh_enrollments(scoped.session, client) == 1
            scoped.session.commit()
            payer = scoped.session.get(PayerRow, payer_row_id)
            assert payer is not None
            assert payer.enrollment_status == "active"
            statuses = scoped.session.execute(
                select(PayerEnrollmentRow.status).where(
                    PayerEnrollmentRow.payer_id == payer_row_id
                )
            ).scalars()
            assert list(statuses) == ["live"]
        finally:
            scoped.close()

        [reminder] = _reminders_visible_to(engine, tenant_schema, _CLINICIAN_A)
        assert reminder.completed_at is not None
