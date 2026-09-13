# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Real-Postgres proof for panel applications.

Three things only a real database shows.

That a freshly-provisioned tenant carries the table at all: provisioning
applies ``tenant_template.sql`` directly rather than running the alembic
chain, so a migration that lands without a regenerated template gives every
new practice a missing table and a 500 far from its cause.

That one clinician cannot read another's applications under the app role,
which is NOBYPASSRLS. This table is row-scoped on purpose and against the
grain of the payer tables beside it — a payer belongs to the practice, but an
application accumulates which panels rejected her and what she is appealing,
and that is not a colleague's business. A guardrail asserted only in a
docstring is a guardrail nobody has tested.

And that the status vocabulary is enforced by the schema rather than by the
callers who happen to write it today.

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

_CLINICIAN_A = "7a9d6e4f-cf6c-6adb-e3c1-c18eb07b7a17"
_CLINICIAN_B = "8b0e7f5a-d07d-7bec-f4d2-d29fc18c8b28"


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

    schema = f"practice_test_panels_{uuid.uuid4().hex[:8]}"
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


def _seed_payer(session: Any, name: str = "Aetna") -> str:
    from app.db.models import PayerRow  # noqa: PLC0415

    now = datetime.now(UTC)
    payer_id = str(uuid.uuid4())
    session.add(PayerRow(id=payer_id, name=name, payer_id="60054", created_at=now, updated_at=now))
    session.flush()
    return payer_id


def _application(user_id: str, payer_id: str, **overrides: Any) -> Any:
    from app.db.models import PanelApplicationRow  # noqa: PLC0415

    now = datetime.now(UTC)
    fields: dict[str, Any] = {
        "id": str(uuid.uuid4()),
        "user_id": user_id,
        "payer_id": payer_id,
        "status": "submitted",
        "action_owner": "pablo",
        "created_at": now,
        "updated_at": now,
    }
    fields.update(overrides)
    return PanelApplicationRow(**fields)


def test_a_fresh_tenant_carries_the_table(engine: Engine, tenant_schema: str) -> None:
    """Provisioning applies the TEMPLATE, not the chain — so it has to be regenerated."""
    with engine.connect() as conn:
        exists = conn.execute(
            text(
                "SELECT 1 FROM information_schema.tables "
                "WHERE table_schema = :schema AND table_name = 'panel_applications'"
            ),
            {"schema": tenant_schema},
        ).scalar()
    assert exists == 1


def test_one_clinician_cannot_read_anothers_applications(
    engine: Engine, tenant_schema: str
) -> None:
    """The reason this table is row-scoped while the payer tables beside it are not."""
    from app.db.models import PanelApplicationRow  # noqa: PLC0415

    scoped = _TenantSession(engine, tenant_schema, _CLINICIAN_A)
    try:
        payer_id = _seed_payer(scoped.session)
        scoped.session.add(_application(_CLINICIAN_A, payer_id))
        scoped.session.commit()
    finally:
        scoped.close()

    scoped = _TenantSession(engine, tenant_schema, _CLINICIAN_B)
    try:
        rows = scoped.session.execute(select(PanelApplicationRow)).scalars().all()
        assert rows == [], "clinician B read clinician A's panel applications"
    finally:
        scoped.close()

    # And A still sees her own — a policy that hides everything from everyone
    # would pass the assertion above while breaking the feature.
    scoped = _TenantSession(engine, tenant_schema, _CLINICIAN_A)
    try:
        rows = scoped.session.execute(select(PanelApplicationRow)).scalars().all()
        assert len(rows) == 1
    finally:
        scoped.close()


def test_a_clinician_cannot_write_an_application_for_somebody_else(
    engine: Engine, tenant_schema: str
) -> None:
    """Reading is only half of it: the policy has to refuse the insert too."""
    scoped = _TenantSession(engine, tenant_schema, _CLINICIAN_A)
    try:
        payer_id = _seed_payer(scoped.session, name="BCBS")
        scoped.session.commit()
    finally:
        scoped.close()

    scoped = _TenantSession(engine, tenant_schema, _CLINICIAN_B)
    try:
        scoped.session.add(_application(_CLINICIAN_A, payer_id))
        with pytest.raises(DBAPIError):
            scoped.session.commit()
    finally:
        scoped.session.rollback()
        scoped.close()


def test_the_status_vocabulary_is_enforced_by_the_schema(
    engine: Engine, tenant_schema: str
) -> None:
    """Not by whichever caller happens to write it today."""
    scoped = _TenantSession(engine, tenant_schema, _CLINICIAN_A)
    try:
        payer_id = _seed_payer(scoped.session, name="Cigna")
        scoped.session.add(_application(_CLINICIAN_A, payer_id, status="having_a_think"))
        with pytest.raises(DBAPIError):
            scoped.session.commit()
    finally:
        scoped.session.rollback()
        scoped.close()


def test_the_action_owner_vocabulary_is_enforced_too(engine: Engine, tenant_schema: str) -> None:
    """`action_owner` is what the whole concierge model turns on; a typo in it
    would silently route a nudge to nobody."""
    scoped = _TenantSession(engine, tenant_schema, _CLINICIAN_A)
    try:
        payer_id = _seed_payer(scoped.session, name="United")
        scoped.session.add(_application(_CLINICIAN_A, payer_id, action_owner="someone"))
        with pytest.raises(DBAPIError):
            scoped.session.commit()
    finally:
        scoped.session.rollback()
        scoped.close()
