# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Real-Postgres proof for payer authorisations.

Two things only a real database shows.

That a freshly-provisioned tenant carries the table: provisioning applies
``tenant_template.sql`` directly rather than running the alembic chain, so a
migration that lands without a regenerated template gives every new practice a
missing table and a 500 far from its cause.

And that one clinician's signature is invisible to another under the app role,
which is NOBYPASSRLS. The row-level policy comes from carrying a ``user_id``
column rather than from a branch written for this table, so it is exactly the
kind of protection that is assumed rather than verified — and this row says
what authority Pablo holds over a named person, which is nobody else's
business even inside her own practice.

Run: ``make test-integration``.
"""

from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, select, text
from sqlalchemy.exc import DBAPIError

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

_CLINICIAN_A = "3c5b2a1d-ab3c-4def-8123-a45bc67d8e90"
_CLINICIAN_B = "4d6c3b2e-bc4d-4ef0-9234-b56cd78e9f01"


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

    schema = f"practice_test_authz_{uuid.uuid4().hex[:8]}"
    create_practice_schema(engine, schema)
    yield schema
    with engine.connect() as conn:
        conn.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        conn.commit()


class _TenantSession:
    """A tenant session armed as one clinician, the way a request opens one."""

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


def _signature(user_id: str, **overrides: Any) -> Any:
    from app.db.models import PayerAuthorizationRow  # noqa: PLC0415

    now = datetime.now(UTC)
    fields: dict[str, Any] = {
        "id": str(uuid.uuid4()),
        "user_id": user_id,
        "version": "2026-09-13",
        "full_text": "# Payer authorisation\n\nThe wording she was shown.\n",
        "signed_name": "Ana Rivera",
        "signed_at": now,
        "created_at": now,
        "updated_at": now,
    }
    fields.update(overrides)
    return PayerAuthorizationRow(**fields)


def test_a_fresh_tenant_carries_the_table(engine: Engine, tenant_schema: str) -> None:
    """Provisioning applies the TEMPLATE, not the chain — so it has to be regenerated."""
    with engine.connect() as conn:
        exists = conn.execute(
            text(
                "SELECT 1 FROM information_schema.tables "
                "WHERE table_schema = :schema AND table_name = 'payer_authorizations'"
            ),
            {"schema": tenant_schema},
        ).scalar()
    assert exists == 1


def test_one_clinician_cannot_read_anothers_signature(engine: Engine, tenant_schema: str) -> None:
    """What authority Pablo holds over a named person is not a colleague's business."""
    from app.db.models import PayerAuthorizationRow  # noqa: PLC0415

    scoped = _TenantSession(engine, tenant_schema, _CLINICIAN_A)
    try:
        scoped.session.add(_signature(_CLINICIAN_A))
        scoped.session.commit()
    finally:
        scoped.close()

    scoped = _TenantSession(engine, tenant_schema, _CLINICIAN_B)
    try:
        rows = scoped.session.execute(select(PayerAuthorizationRow)).scalars().all()
        assert rows == [], "clinician B read clinician A's payer authorisation"
    finally:
        scoped.close()

    # And A still sees her own — a policy that hides everything from everyone
    # would pass the assertion above while shutting the gate on its owner.
    scoped = _TenantSession(engine, tenant_schema, _CLINICIAN_A)
    try:
        rows = scoped.session.execute(select(PayerAuthorizationRow)).scalars().all()
        assert len(rows) == 1
    finally:
        scoped.close()


def test_a_clinician_cannot_sign_on_somebody_elses_behalf(
    engine: Engine, tenant_schema: str
) -> None:
    """The write half. Forging a colleague's authorisation is the worst case here:
    it would hand Pablo apparent authority nobody granted."""
    scoped = _TenantSession(engine, tenant_schema, _CLINICIAN_B)
    try:
        scoped.session.add(_signature(_CLINICIAN_A))
        with pytest.raises(DBAPIError):
            scoped.session.commit()
    finally:
        scoped.session.rollback()
        scoped.close()
