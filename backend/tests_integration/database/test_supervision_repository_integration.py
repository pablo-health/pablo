# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Real-Postgres tests for ``PostgresSupervisionRepository``.

The unit suite (``test_supervision_repository.py``) drives the repository with
a ``MagicMock`` session: it proves each method calls the session as expected,
but never exercises the real ORM mapping, the ``compliance_items`` foreign-key
link, the user-scoping filters, or the ``ORDER BY`` clauses against actual SQL.
A repository whose call shape drifts from the real method — the failure a mocked
collaborator hides — would pass that suite while failing in production.

These tests close the gap end-to-end against a provisioned tenant schema, using
the same context the app sets per request:

* ``set_tenant_schema`` arms ``_current_tenant_schema``; the ``Engine``
  "checkout" listener re-applies ``search_path`` on every pooled connection, so
  the schema survives the commits these tests make (mirroring a real request's
  mid-flight commits) rather than resetting to the neutral baseline.
* ``arm_current_user_id`` stashes the clinician id on ``session.info``; the
  ``after_begin`` listener re-applies the ``app.current_user_id`` GUC on every
  transaction. ``supervision_relationships`` / ``supervision_hours`` /
  ``compliance_items`` carry a ``user_id`` column with no ``patient_id``, so
  provisioning gives them a direct ``rls_user_isolation`` policy under FORCE
  RLS — the NOBYPASSRLS test role sees a row only when the GUC matches its
  ``user_id``. The cross-user assertions therefore prove both the repository's
  own ``user_id`` filter and the database RLS policy.

Run: ``make test-integration``.
"""

from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from alembic import command
from alembic.config import Config
from app.db import (
    _current_tenant_schema,
    arm_current_user_id,
    set_tenant_schema,
)
from app.repositories.postgres.supervision import (
    PostgresSupervisionRepository,
    SupervisionHours,
    SupervisionRelationship,
)
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

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

    schema = f"practice_test_supervision_{uuid.uuid4().hex[:8]}"
    create_practice_schema(engine, schema)
    yield schema
    with engine.connect() as conn:
        conn.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        conn.commit()


@pytest.fixture
def session(engine: Engine, tenant_schema: str) -> Iterator[Session]:
    """A tenant-scoped session, set up the way a request is.

    Each test uses fresh ``user_id`` values (so no cross-test cleanup is
    needed) and arms the GUC per user via :func:`_arm`.
    """
    sess = Session(bind=engine)
    set_tenant_schema(sess, tenant_schema)
    try:
        yield sess
    finally:
        sess.rollback()
        sess.close()
        _current_tenant_schema.set(None)


def _arm(session: Session, user_id: str) -> None:
    """Arm the RLS GUC for the statements that follow, as the app does per
    request. Commits first so the next statement opens a fresh transaction
    whose ``after_begin`` listener applies the newly-armed user (the search
    path is re-applied from the ContextVar on the next checkout)."""
    session.commit()
    arm_current_user_id(session, user_id)


def _user() -> str:
    return str(uuid.uuid4())


def _make_relationship(
    *,
    user_id: str,
    supervisor_name: str = "Dr. Pat Lee",
    next_review_date: str | None = "2027-01-01",
    status: str = "active",
    created_at: datetime | None = None,
) -> SupervisionRelationship:
    now = created_at or datetime.now(UTC)
    return SupervisionRelationship(
        id=str(uuid.uuid4()),
        user_id=user_id,
        compliance_item_id=None,
        relationship_type="physician_delegation",
        supervisor_name=supervisor_name,
        supervisor_credential="MD",
        supervisor_dea="XL1234563",
        supervisor_license="MI-12345",
        state="MI",
        effective_date="2026-01-01",
        review_cadence_days=365,
        next_review_date=next_review_date,
        authority_ref="REF-1",
        status=status,
        notes=None,
        created_at=now,
        updated_at=now,
    )


def _make_hours(
    *,
    relationship_id: str,
    user_id: str,
    logged_date: str,
    hours: str = "1.50",
    kind: str = "direct",
) -> SupervisionHours:
    now = datetime.now(UTC)
    return SupervisionHours(
        id=str(uuid.uuid4()),
        supervision_relationship_id=relationship_id,
        user_id=user_id,
        logged_date=logged_date,
        hours=Decimal(hours),
        kind=kind,
        supervisor="Dr. Pat Lee",
        notes=None,
        created_at=now,
        updated_at=now,
    )


def test_create_relationship_persists_and_links_review_item(session: Session) -> None:
    repo = PostgresSupervisionRepository(session)
    user_id = _user()
    _arm(session, user_id)

    rel = _make_relationship(user_id=user_id, next_review_date="2027-03-15")
    returned = repo.create_relationship(rel, review_item_label="Annual delegation review")
    session.commit()

    # A linked compliance_items row was created and back-referenced.
    assert returned.compliance_item_id is not None
    item = (
        session.execute(
            text("SELECT user_id, item_type, label, due_date FROM compliance_items WHERE id = :id"),
            {"id": returned.compliance_item_id},
        )
        .mappings()
        .one()
    )
    assert item["user_id"] == user_id
    assert item["item_type"] == "supervision_review"
    assert item["label"] == "Annual delegation review"
    assert item["due_date"] == "2027-03-15"  # mirrors next_review_date

    # The relationship round-trips through the real ORM with the FK persisted.
    fetched = repo.list_by_user(user_id)
    assert len(fetched) == 1
    assert fetched[0].compliance_item_id == returned.compliance_item_id
    assert fetched[0].relationship_type == "physician_delegation"
    assert fetched[0].state == "MI"


def test_create_relationship_without_label_creates_no_review_item(
    session: Session,
) -> None:
    repo = PostgresSupervisionRepository(session)
    user_id = _user()
    _arm(session, user_id)

    # next_review_date is set, but no review_item_label → no item is created.
    rel = _make_relationship(user_id=user_id, next_review_date="2027-03-15")
    returned = repo.create_relationship(rel)
    session.commit()

    assert returned.compliance_item_id is None
    count = session.execute(
        text("SELECT count(*) FROM compliance_items WHERE user_id = :u"),
        {"u": user_id},
    ).scalar_one()
    assert count == 0


def test_list_by_user_orders_by_created_at_and_isolates(session: Session) -> None:
    repo = PostgresSupervisionRepository(session)
    user_a, user_b = _user(), _user()
    base = datetime.now(UTC)

    # Insert out of chronological order to prove the ORDER BY, not insert order.
    _arm(session, user_a)
    repo.create_relationship(
        _make_relationship(
            user_id=user_a, supervisor_name="Second", created_at=base + timedelta(hours=1)
        )
    )
    repo.create_relationship(
        _make_relationship(user_id=user_a, supervisor_name="First", created_at=base)
    )
    _arm(session, user_b)
    repo.create_relationship(_make_relationship(user_id=user_b, supervisor_name="Other"))
    session.commit()

    _arm(session, user_a)
    rows = repo.list_by_user(user_a)
    assert [r.supervisor_name for r in rows] == ["First", "Second"]  # created_at order
    assert all(r.user_id == user_a for r in rows)  # user_b's row is not visible


def test_get_and_delete_enforce_user_scope(session: Session) -> None:
    repo = PostgresSupervisionRepository(session)
    owner, intruder = _user(), _user()
    _arm(session, owner)
    rel = _make_relationship(user_id=owner)
    repo.create_relationship(rel)
    session.commit()

    # With the owner armed (RLS permits), the repository's own user_id check
    # still refuses a non-owner caller.
    assert repo.get(rel.id, intruder) is None
    assert repo.delete(rel.id, intruder) is False
    assert repo.get(rel.id, owner) is not None

    # The owner can delete; the row is gone.
    assert repo.delete(rel.id, owner) is True
    session.commit()
    assert repo.get(rel.id, owner) is None
    assert repo.list_by_user(owner) == []


def test_update_inserts_when_relationship_absent(session: Session) -> None:
    repo = PostgresSupervisionRepository(session)
    user_id = _user()
    _arm(session, user_id)

    # update() on a never-persisted relationship falls through to an insert.
    rel = _make_relationship(user_id=user_id, status="pending")
    repo.update(rel)
    session.commit()

    rows = repo.list_by_user(user_id)
    assert len(rows) == 1
    assert rows[0].id == rel.id
    assert rows[0].status == "pending"

    # A subsequent update mutates in place rather than inserting again.
    rel.status = "active"
    repo.update(rel)
    session.commit()
    rows = repo.list_by_user(user_id)
    assert len(rows) == 1
    assert rows[0].status == "active"


def test_add_and_list_hours_orders_by_logged_date_and_isolates(
    session: Session,
) -> None:
    repo = PostgresSupervisionRepository(session)
    user_a, user_b = _user(), _user()

    _arm(session, user_a)
    rel_a = _make_relationship(user_id=user_a)
    repo.create_relationship(rel_a)
    # Insert out of date order to prove the ORDER BY logged_date.
    repo.add_hours(_make_hours(relationship_id=rel_a.id, user_id=user_a, logged_date="2026-06-10"))
    repo.add_hours(_make_hours(relationship_id=rel_a.id, user_id=user_a, logged_date="2026-06-01"))
    _arm(session, user_b)
    rel_b = _make_relationship(user_id=user_b)
    repo.create_relationship(rel_b)
    repo.add_hours(_make_hours(relationship_id=rel_b.id, user_id=user_b, logged_date="2026-06-05"))
    session.commit()

    _arm(session, user_a)
    rows = repo.list_hours(rel_a.id, user_a)
    assert [r.logged_date for r in rows] == ["2026-06-01", "2026-06-10"]
    assert all(r.user_id == user_a for r in rows)
    # The (relationship_id, user_id) scoping rejects a cross-user read.
    assert repo.list_hours(rel_a.id, user_b) == []
