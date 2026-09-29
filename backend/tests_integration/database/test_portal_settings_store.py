# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Whether a practice offers the portal, against real PostgreSQL.

Three things a unit test cannot prove:

* the platform store round-trips through ``platform.practice_portal_settings``,
  keeps the FIRST decision time, and is keyed on the practice;
* the schema-keyed reader the portal's doors use joins to the right practice;
* the migration that introduced the table gives every existing practice the
  portal, including one that has never minted an address, and skips a
  deleted one.

The unit suite (``tests/test_portal_settings_routes.py``) covers the routes.
"""

from __future__ import annotations

import importlib.util
import os
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from app.db import DEFAULT_PRACTICE_SCHEMA, PLATFORM_SCHEMA
from app.db.platform_models import PlatformBase
from app.portal.portal_settings import (
    NOT_OFFERED,
    PlatformPortalSettingsStore,
    portal_enabled_for_practice,
    portal_enabled_for_schema,
)
from sqlalchemy import create_engine, text

if TYPE_CHECKING:
    from collections.abc import Iterator

    from sqlalchemy.engine import Engine

_MIGRATION = (
    Path(__file__).resolve().parents[2]
    / "alembic_platform"
    / "versions"
    / "b5f1c8d3a702_practice_portal_settings.py"
)


@pytest.fixture(scope="module")
def engine() -> Iterator[Engine]:
    eng = create_engine(os.environ["DATABASE_URL"], pool_pre_ping=True)
    with eng.begin() as conn:
        conn.execute(text(f"CREATE SCHEMA IF NOT EXISTS {PLATFORM_SCHEMA}"))
        conn.execute(text(f"CREATE SCHEMA IF NOT EXISTS {DEFAULT_PRACTICE_SCHEMA}"))
        PlatformBase.metadata.create_all(conn)
    yield eng
    eng.dispose()


def _new_practice(engine: Engine, *, deleted: bool = False) -> tuple[str, str]:
    practice_id = f"practice-portal-{uuid.uuid4().hex[:8]}"
    schema = f"practice_{uuid.uuid4().hex[:12]}"
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO platform.practices "
                "(id, name, schema_name, owner_email, created_at, deleted_at) "
                "VALUES (:id, :name, :schema, :email, :now, :deleted)"
            ),
            {
                "id": practice_id,
                "name": "Example Therapy",
                "schema": schema,
                "email": f"{practice_id}@example.test",
                "now": datetime.now(UTC),
                "deleted": datetime.now(UTC) if deleted else None,
            },
        )
    return practice_id, schema


def _run_migration(engine: Engine) -> None:
    spec = importlib.util.spec_from_file_location("practice_portal_settings", _MIGRATION)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    with engine.begin() as conn, Operations.context(MigrationContext.configure(conn)):
        module.upgrade()


def _settings_row(engine: Engine, practice_id: str) -> tuple[bool, datetime | None] | None:
    with engine.begin() as conn:
        row = conn.execute(
            text(
                "SELECT enabled, decided_at FROM platform.practice_portal_settings "
                "WHERE practice_id = :id"
            ),
            {"id": practice_id},
        ).one_or_none()
    return None if row is None else (row.enabled, row.decided_at)


# ── the store ───────────────────────────────────────────────────────────


def test_a_practice_with_no_row_does_not_offer_the_portal(engine: Engine) -> None:
    practice_id, schema = _new_practice(engine)

    assert PlatformPortalSettingsStore().get(practice_id) == NOT_OFFERED
    assert portal_enabled_for_practice(practice_id) is False
    assert portal_enabled_for_schema(schema) is False


def test_turning_it_on_and_off_keeps_the_first_decision_time(engine: Engine) -> None:
    store = PlatformPortalSettingsStore()
    practice_id, schema = _new_practice(engine)

    on = store.set_enabled(practice_id, enabled=True, by="clinician-1")
    assert on.enabled is True
    assert on.decided_at is not None
    assert portal_enabled_for_schema(schema) is True

    off = store.set_enabled(practice_id, enabled=False, by="clinician-2")
    assert off.enabled is False
    assert off.decided_at == on.decided_at
    assert portal_enabled_for_schema(schema) is False


def test_one_practices_setting_never_reaches_another(engine: Engine) -> None:
    store = PlatformPortalSettingsStore()
    ours, our_schema = _new_practice(engine)
    theirs, their_schema = _new_practice(engine)

    store.set_enabled(ours, enabled=True, by="clinician-1")

    assert portal_enabled_for_schema(our_schema) is True
    assert portal_enabled_for_schema(their_schema) is False
    assert store.get(theirs) == NOT_OFFERED


@pytest.mark.usefixtures("engine")
def test_an_unknown_schema_is_off() -> None:
    assert portal_enabled_for_schema("practice_does_not_exist") is False


# ── the migration ───────────────────────────────────────────────────────


def test_the_migration_gives_every_existing_practice_the_portal(engine: Engine) -> None:
    """Existing practices keep the portal they had by default — including a
    practice that has never minted an address — and are not asked again. A
    deleted practice gets nothing. Running it twice changes nothing."""
    never_invited, never_invited_schema = _new_practice(engine)
    deleted, _ = _new_practice(engine, deleted=True)
    already_off, _ = _new_practice(engine)
    PlatformPortalSettingsStore().set_enabled(already_off, enabled=False, by="clinician-1")

    _run_migration(engine)

    row = _settings_row(engine, never_invited)
    assert row is not None
    enabled, decided_at = row
    assert enabled is True
    assert decided_at is not None
    assert portal_enabled_for_schema(never_invited_schema) is True
    assert _settings_row(engine, deleted) is None
    # A practice that already answered keeps its answer.
    assert _settings_row(engine, already_off) is not None
    assert PlatformPortalSettingsStore().get(already_off).enabled is False

    _run_migration(engine)
    assert _settings_row(engine, never_invited) == row


def test_a_practice_created_after_the_migration_starts_off(engine: Engine) -> None:
    _run_migration(engine)
    practice_id, schema = _new_practice(engine)

    assert PlatformPortalSettingsStore().get(practice_id) == NOT_OFFERED
    assert portal_enabled_for_schema(schema) is False
