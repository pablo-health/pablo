# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Conftest for single-practice conversion."""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

import pytest
from sqlalchemy import create_engine, text

if TYPE_CHECKING:
    from collections.abc import Iterator

    from sqlalchemy.engine import Engine

    from .types import IdentityFixture


@pytest.fixture
def old_shape(engine: Engine) -> Iterator[Engine]:
    """A deployment as it looked before the template and live practice split.

    ``practice`` holds the charts and the registry points at it. Restored
    afterwards so the session-scoped template database is left as found.
    """
    from app.db import (  # noqa: PLC0415
        DEFAULT_PRACTICE_ID,
        DEFAULT_PRACTICE_OWN_SCHEMA,
        DEFAULT_PRACTICE_SCHEMA,
        PLATFORM_SCHEMA,
    )
    from app.db.provisioning import create_practice_schema  # noqa: PLC0415

    # Park whatever the shared database already holds under these two names so
    # this test owns them outright. Everything after the first rename runs inside
    # the try, so a setup failure still restores — a leaked ``_parked_template``
    # breaks every later test in the module with DuplicateSchema, which reads as
    # eleven broken tests rather than the one that actually failed.
    with engine.begin() as conn:
        conn.execute(text(f'ALTER SCHEMA "{DEFAULT_PRACTICE_SCHEMA}" RENAME TO "_parked_template"'))
        parked_own = bool(
            conn.execute(
                text("SELECT 1 FROM information_schema.schemata WHERE schema_name = :s"),
                {"s": DEFAULT_PRACTICE_OWN_SCHEMA},
            ).first()
        )
        if parked_own:
            conn.execute(
                text(f'ALTER SCHEMA "{DEFAULT_PRACTICE_OWN_SCHEMA}" RENAME TO "_parked_own"')
            )

    try:
        create_practice_schema(engine, DEFAULT_PRACTICE_SCHEMA)
        with engine.begin() as conn:
            conn.execute(
                text(
                    f"INSERT INTO {PLATFORM_SCHEMA}.practices "  # noqa: S608 — schema/table are validated identifiers, never user input
                    f"(id, name, schema_name, owner_email, product, status, "
                    f"provisioning_status, is_active, created_at) "
                    f"VALUES (:id, 'Default Practice', :schema, '', 'pablo', 'active', "
                    f"'ready', true, now()) "
                    f"ON CONFLICT (id) DO UPDATE SET schema_name = EXCLUDED.schema_name"
                ),
                {"id": DEFAULT_PRACTICE_ID, "schema": DEFAULT_PRACTICE_SCHEMA},
            )
        yield engine
    finally:
        with engine.begin() as conn:
            for name in (DEFAULT_PRACTICE_SCHEMA, DEFAULT_PRACTICE_OWN_SCHEMA):
                conn.execute(text(f'DROP SCHEMA IF EXISTS "{name}" CASCADE'))
            conn.execute(
                text(f"DELETE FROM {PLATFORM_SCHEMA}.practices WHERE id = :id"),  # noqa: S608
                {"id": DEFAULT_PRACTICE_ID},
            )
            conn.execute(
                text(f'ALTER SCHEMA "_parked_template" RENAME TO "{DEFAULT_PRACTICE_SCHEMA}"')
            )
            if parked_own:
                conn.execute(
                    text(f'ALTER SCHEMA "_parked_own" RENAME TO "{DEFAULT_PRACTICE_OWN_SCHEMA}"')
                )


@pytest.fixture(scope="module")
def superuser_engine() -> Iterator[Engine]:
    """A connection that can see past the policies, for counting what is ON DISK.

    The suite's ``pablo`` role is deliberately NOSUPERUSER NOBYPASSRLS — that is
    the whole point of the RLS invariants — so it cannot answer "did every row
    survive the move". Under FORCE RLS with no GUC armed it correctly sees zero,
    which is the right answer to a different question.

    So the physical-preservation assertions use the container's bootstrap
    superuser. Visibility THROUGH the policies is asserted separately, as each
    owner, in ``test_migration_leaves_every_row_readable_by_its_owner`` — the two
    together are what "nothing was lost and everything is still reachable" means.
    """
    _db_url = os.environ["DATABASE_URL"]

    su_url = _db_url.replace("pablo:pablo_dev@", "postgres:postgres_dev@", 1)
    if su_url == _db_url:
        pytest.skip(
            "cannot derive the bootstrap superuser URL from DATABASE_URL; these "
            "assertions need a role that can read past RLS"
        )
    eng = create_engine(su_url, pool_pre_ping=True)
    yield eng
    eng.dispose()


@pytest.fixture
def identities(old_shape: Engine) -> Iterator[IdentityFixture]:
    """Seed identities and clean them up, whatever the test does to them."""
    from .helpers import _clear_identities, _seed_identity  # noqa: PLC0415

    engine = old_shape
    seeded: list[str] = []

    def _seed(email: str, *, as_user: bool = True, with_mapping: bool = False) -> str:
        with engine.begin() as conn:
            _seed_identity(conn, email=email, as_user=as_user, with_mapping=with_mapping)
        seeded.append(email)
        return email

    try:
        yield engine, _seed
    finally:
        with engine.begin() as conn:
            _clear_identities(conn, seeded)
