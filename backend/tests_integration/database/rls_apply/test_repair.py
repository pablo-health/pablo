# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Repair missing, changed, and unexpected row security."""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from app.db.rls_apply import DesiredTable
    from sqlalchemy.engine import Engine

from .helpers import (
    _COLLEAGUE,
    _TREATING,
    _apply,
    _ddl,
    _flags,
    _patients_read_by,
    _policies,
)

_DB_URL = os.environ.get("DATABASE_URL", "")


pytestmark = pytest.mark.skipif(
    not _DB_URL or os.environ.get("DATABASE_BACKEND") != "postgres",
    reason=(
        "PostgreSQL not configured. Set DATABASE_URL and DATABASE_BACKEND=postgres "
        "or run via make test-integration."
    ),
)


def test_a_missing_policy_is_created(
    engine: Engine, practice: str, desired: dict[str, DesiredTable]
) -> None:
    assert _patients_read_by(engine, practice, _TREATING) == 2
    _ddl(engine, f'DROP POLICY rls_patient_access ON "{practice}".patients')
    assert _patients_read_by(engine, practice, _TREATING) == 0, "the damage was not done"

    counts = _apply(engine, practice, desired)

    assert counts.altered == 1
    assert _patients_read_by(engine, practice, _TREATING) == 2
    assert _patients_read_by(engine, practice, _COLLEAGUE) == 0


def test_a_policy_the_sql_does_not_have_is_dropped(
    engine: Engine, practice: str, desired: dict[str, DesiredTable]
) -> None:
    _ddl(engine, f'CREATE POLICY rls_everyone ON "{practice}".patients FOR SELECT USING (true)')
    assert _patients_read_by(engine, practice, _COLLEAGUE) == 2, "the damage was not done"

    counts = _apply(engine, practice, desired)

    assert counts.altered == 1
    assert _patients_read_by(engine, practice, _COLLEAGUE) == 0
    assert _patients_read_by(engine, practice, _TREATING) == 2
    assert ("patients", "rls_everyone") not in _policies(engine, practice)


def test_a_policy_edited_by_hand_is_put_back(
    engine: Engine, practice: str, reference: str, desired: dict[str, DesiredTable]
) -> None:
    """``ALTER POLICY`` keeps the name and the comment, so only the body shows it."""
    _ddl(engine, f'ALTER POLICY rls_patient_access ON "{practice}".patients USING (true)')
    assert _patients_read_by(engine, practice, _COLLEAGUE) == 2, "the damage was not done"

    counts = _apply(engine, practice, desired)

    assert counts.altered == 1
    assert _patients_read_by(engine, practice, _COLLEAGUE) == 0
    assert _patients_read_by(engine, practice, _TREATING) == 2
    assert _policies(engine, practice) == _policies(engine, reference)


def test_a_table_the_sql_leaves_unscoped_has_row_security_turned_off(
    engine: Engine, practice: str, desired: dict[str, DesiredTable]
) -> None:
    live = _flags(engine, practice)
    table = next(
        name
        for name in sorted(desired)
        if not desired[name].enabled and name in live and name != "alembic_version"
    )
    _ddl(engine, f'ALTER TABLE "{practice}"."{table}" ENABLE ROW LEVEL SECURITY')
    _ddl(engine, f'ALTER TABLE "{practice}"."{table}" FORCE ROW LEVEL SECURITY')
    assert _flags(engine, practice)[table] == (True, True)

    counts = _apply(engine, practice, desired)

    assert counts.altered == 1
    assert _flags(engine, practice)[table] == (False, False)


def test_a_table_no_sql_creates_is_left_exactly_as_it_is(
    engine: Engine, practice: str, desired: dict[str, DesiredTable]
) -> None:
    _ddl(engine, f'CREATE TABLE "{practice}".added_later (id uuid PRIMARY KEY, owner text)')
    _ddl(engine, f'ALTER TABLE "{practice}".added_later ENABLE ROW LEVEL SECURITY')
    _ddl(engine, f'CREATE POLICY its_own ON "{practice}".added_later USING (owner = current_user)')
    before = (_policies(engine, practice), _flags(engine, practice))

    counts = _apply(engine, practice, desired)

    assert counts.skipped == 1
    assert counts.altered == 0
    assert (_policies(engine, practice), _flags(engine, practice)) == before
