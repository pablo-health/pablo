# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Test move for single-practice conversion."""

from __future__ import annotations

import os
import uuid
from typing import TYPE_CHECKING

import pytest
from app.db import (
    DEFAULT_PRACTICE_OWN_SCHEMA,
    DEFAULT_PRACTICE_SCHEMA,
)
from app.db.single_practice_migration import (
    is_migrated,
    migrate,
)
from sqlalchemy import text

from .helpers import _count, _counts, _seed_patient

_db_url = os.environ.get("DATABASE_URL", "")
pytestmark = pytest.mark.skipif(
    not _db_url or os.environ.get("DATABASE_BACKEND") != "postgres",
    reason=(
        "PostgreSQL not configured. Set DATABASE_URL and "
        "DATABASE_BACKEND=postgres; testcontainers should set both."
    ),
)


if TYPE_CHECKING:
    from sqlalchemy.engine import Engine


def test_migration_preserves_every_row_per_table(
    old_shape: Engine, superuser_engine: Engine
) -> None:
    """Counts before and after, per table — not a spot check.

    A spot check on a chart that survived says nothing about one that did not,
    and "some rows are still there" is exactly what a partial loss looks like.
    """
    engine = old_shape
    user_a, user_b = str(uuid.uuid4()), str(uuid.uuid4())
    with engine.begin() as conn:
        for _ in range(4):
            _seed_patient(conn, DEFAULT_PRACTICE_SCHEMA, user_a)
        for _ in range(2):
            _seed_patient(conn, DEFAULT_PRACTICE_SCHEMA, user_b)

    def table_counts(schema: str) -> dict[str, int]:
        out: dict[str, int] = {}
        with engine.connect() as conn:
            tables = [
                r[0]
                for r in conn.execute(
                    text(
                        "SELECT table_name FROM information_schema.tables "
                        "WHERE table_schema = :s AND table_type = 'BASE TABLE'"
                    ),
                    {"s": schema},
                )
            ]
        for t in tables:
            out[t] = _count(superuser_engine, schema, t)
        return out

    before = table_counts(DEFAULT_PRACTICE_SCHEMA)
    migrate(engine)
    after = table_counts(DEFAULT_PRACTICE_OWN_SCHEMA)

    assert before == after, {
        t: (before.get(t), after.get(t))
        for t in set(before) | set(after)
        if before.get(t) != after.get(t)
    }
    assert before["patients"] == 6


def test_migration_leaves_every_row_readable_by_its_owner(old_shape: Engine) -> None:
    """The end state that matters: with policies on, each clinician still sees
    their own charts — through the policy, as the app would."""
    engine = old_shape
    user_a, user_b = str(uuid.uuid4()), str(uuid.uuid4())
    with engine.begin() as conn:
        for _ in range(4):
            _seed_patient(conn, DEFAULT_PRACTICE_SCHEMA, user_a)
        for _ in range(2):
            _seed_patient(conn, DEFAULT_PRACTICE_SCHEMA, user_b)

    migrate(engine)

    for user_id, expected in ((user_a, 4), (user_b, 2)):
        with engine.connect() as conn:
            conn.execute(text(f'SET search_path = "{DEFAULT_PRACTICE_OWN_SCHEMA}"'))
            conn.execute(
                text("SELECT set_config('app.current_user_id', :u, false)"), {"u": user_id}
            )
            visible = conn.execute(text("SELECT count(*) FROM patients")).scalar_one()
        assert visible == expected, f"{user_id} sees {visible} of their {expected} patients"


def test_rls_is_actually_on_afterwards(old_shape: Engine) -> None:
    engine = old_shape
    with engine.begin() as conn:
        _seed_patient(conn, DEFAULT_PRACTICE_SCHEMA, str(uuid.uuid4()))

    migrate(engine)

    with engine.connect() as conn:
        forced = conn.execute(
            text(
                "SELECT c.relrowsecurity, c.relforcerowsecurity FROM pg_class c "
                "JOIN pg_namespace n ON n.oid = c.relnamespace "
                "WHERE n.nspname = :s AND c.relname = 'patients'"
            ),
            {"s": DEFAULT_PRACTICE_OWN_SCHEMA},
        ).one()
    assert forced == (True, True), "patients is not force-RLS'd after the migration"


def test_the_template_comes_back(old_shape: Engine, superuser_engine: Engine) -> None:
    """The migration renames the template away; every future practice is cloned
    from it, so it has to be rebuilt — empty, and a template again."""
    engine = old_shape
    with engine.begin() as conn:
        _seed_patient(conn, DEFAULT_PRACTICE_SCHEMA, str(uuid.uuid4()))

    migrate(engine)

    with engine.connect() as conn:
        assert conn.execute(
            text("SELECT 1 FROM information_schema.schemata WHERE schema_name = :s"),
            {"s": DEFAULT_PRACTICE_SCHEMA},
        ).first(), "the template schema was not rebuilt"
        assert (
            conn.execute(
                text(f"SELECT count(*) FROM {DEFAULT_PRACTICE_SCHEMA}.patients")  # noqa: S608
            ).scalar_one()
            == 0
        ), "the rebuilt template is not empty"


def test_running_it_twice_is_safe(old_shape: Engine, superuser_engine: Engine) -> None:
    engine = old_shape
    with engine.begin() as conn:
        for _ in range(3):
            _seed_patient(conn, DEFAULT_PRACTICE_SCHEMA, str(uuid.uuid4()))

    migrate(engine)
    assert is_migrated(engine)

    migrate(engine)  # must not raise, must not move anything

    assert _count(superuser_engine, DEFAULT_PRACTICE_OWN_SCHEMA, "patients") == 3


def test_force_migrates_despite_orphans(old_shape: Engine, superuser_engine: Engine) -> None:
    """``--force`` is for an operator who read the report and decided. It skips
    the refusal, not the counting."""
    engine = old_shape
    user_id = str(uuid.uuid4())
    with engine.begin() as conn:
        _seed_patient(conn, DEFAULT_PRACTICE_SCHEMA, user_id, with_grant=True)
        _seed_patient(conn, DEFAULT_PRACTICE_SCHEMA, user_id, with_grant=False)

    counts = migrate(engine, force=True)

    assert _counts(counts, "patients").orphaned == 1
    assert is_migrated(engine)
    # The orphan is still ON DISK — force means "migrate anyway", not "delete".
    assert _count(superuser_engine, DEFAULT_PRACTICE_OWN_SCHEMA, "patients") == 2
