# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Equivalence, ownership, read-only reporting, and transaction boundaries."""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

import pytest
from app.db.rls_apply import (
    DesiredTable,
    apply_tenant_rls,
    tenant_rls_differences,
)
from sqlalchemy import text
from sqlalchemy.orm import Session as OrmSession

if TYPE_CHECKING:
    from sqlalchemy.engine import Engine

from .helpers import (
    _COLLEAGUE,
    _DIRECTORY_POLICY,
    _DIRECTORY_TABLES,
    _LEAVE,
    _TREATING,
    _apply,
    _ddl,
    _flags,
    _patients_read_by,
    _policies,
    _policy_oids,
)

_DB_URL = os.environ.get("DATABASE_URL", "")


pytestmark = pytest.mark.skipif(
    not _DB_URL or os.environ.get("DATABASE_BACKEND") != "postgres",
    reason=(
        "PostgreSQL not configured. Set DATABASE_URL and DATABASE_BACKEND=postgres "
        "or run via make test-integration."
    ),
)


def test_the_connecting_role_does_not_bypass_rls(engine: Engine) -> None:
    """If this fails, every read assertion below is meaningless."""
    with engine.connect() as conn:
        row = conn.execute(
            text("SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname = current_user")
        ).one()
    assert not row[0]
    assert not row[1]


def test_a_practice_the_reconcile_just_finished_needs_nothing(
    engine: Engine, practice: str, desired: dict[str, DesiredTable]
) -> None:
    """Provisioning applied the template and ran the existing reconcile over it.

    If the applier finds anything to change here, it and the reconcile disagree
    about what the template means, and switching one for the other would change
    live policies. The OIDs say more than the count: not one policy was dropped
    and created again.
    """
    before = (_policies(engine, practice), _flags(engine, practice))
    oids = _policy_oids(engine, practice)

    counts = _apply(engine, practice, desired)

    assert counts.altered == 0
    assert counts.skipped == 0
    assert counts.applied == len(desired)
    assert _policy_oids(engine, practice) == oids
    assert (_policies(engine, practice), _flags(engine, practice)) == before


def test_the_directory_policy_is_kept_only_because_the_caller_names_it(
    engine: Engine, practice: str, desired: dict[str, DesiredTable]
) -> None:
    """The template has no directory policy, so it stays only when ``leave`` says so.

    The second half is the rule with nothing left out: a live policy the SQL
    does not have, on a table the SQL creates, is dropped.
    """

    def directory_tables() -> set[str]:
        return {
            table for table, policy in _policies(engine, practice) if policy == _DIRECTORY_POLICY
        }

    assert directory_tables() == _DIRECTORY_TABLES
    assert all(_DIRECTORY_POLICY not in desired[table].policies for table in _DIRECTORY_TABLES)

    assert _apply(engine, practice, desired).altered == 0
    assert directory_tables() == _DIRECTORY_TABLES

    assert _apply(engine, practice, desired, leave=()).altered == len(_DIRECTORY_TABLES)
    assert directory_tables() == set()


def test_a_practice_with_no_row_security_at_all_ends_up_as_the_file_says(
    engine: Engine, practice: str, from_the_file: str, desired: dict[str, DesiredTable]
) -> None:
    for table, policy in _policies(engine, practice):
        if policy != _DIRECTORY_POLICY:
            _ddl(engine, f'DROP POLICY "{policy}" ON "{practice}"."{table}"')
    for table, (enabled, _forced) in _flags(engine, practice).items():
        if enabled:
            _ddl(engine, f'ALTER TABLE "{practice}"."{table}" NO FORCE ROW LEVEL SECURITY')
            _ddl(engine, f'ALTER TABLE "{practice}"."{table}" DISABLE ROW LEVEL SECURITY')
    assert {policy for _table, policy in _policies(engine, practice)} == {_DIRECTORY_POLICY}
    forced = sum(table.forced for table in desired.values())

    counts = _apply(engine, practice, desired)

    assert counts.altered == forced
    rebuilt = {
        key: value
        for key, value in _policies(engine, practice).items()
        if key[1] != _DIRECTORY_POLICY
    }
    assert rebuilt == _policies(engine, from_the_file)
    assert _flags(engine, practice) == _flags(engine, from_the_file)
    assert _patients_read_by(engine, practice, _TREATING) == 2
    assert _patients_read_by(engine, practice, _COLLEAGUE) == 0

    # Some of those policies read back unlike the statement that made them.
    # The second pass has to recognise them as its own work and leave them.
    oids = _policy_oids(engine, practice)
    assert _apply(engine, practice, desired).altered == 0
    assert _policy_oids(engine, practice) == oids, "a second pass rebuilt a policy"


def test_a_schema_built_from_the_file_settles_after_one_pass(
    engine: Engine, from_the_file: str, desired: dict[str, DesiredTable]
) -> None:
    """What a practice provisioned from the template alone goes through.

    Its policies were created by the file's statements, so wherever Postgres
    stores a statement differently the first pass rebuilds that policy to
    record what it became. Nothing about the policy changes, so nothing is
    counted, and the pass after that touches nothing.
    """
    first = _policies(engine, from_the_file)
    oids = _policy_oids(engine, from_the_file)

    assert _apply(engine, from_the_file, desired).altered == 0
    assert _policies(engine, from_the_file) == first
    recorded = _policy_oids(engine, from_the_file)
    assert recorded != oids, (
        "no policy needed its stored form recorded; this test no longer covers that path"
    )

    assert _apply(engine, from_the_file, desired).altered == 0
    assert _policy_oids(engine, from_the_file) == recorded


def test_nothing_is_kept_unless_the_caller_commits(
    engine: Engine, practice: str, desired: dict[str, DesiredTable]
) -> None:
    _ddl(engine, f'DROP POLICY rls_patient_access ON "{practice}".patients')

    with OrmSession(engine) as session:
        session.execute(text(f"SET search_path = {practice}, platform, public"))
        path = session.execute(text("SHOW search_path")).scalar_one()
        counts = apply_tenant_rls(session, practice, desired, leave=_LEAVE)
        assert counts.altered == 1
        # It ran its statements under an empty path and put the caller's back.
        assert session.execute(text("SHOW search_path")).scalar_one() == path
        session.rollback()

    assert ("patients", "rls_patient_access") not in _policies(engine, practice)


def test_a_table_the_sql_creates_that_the_schema_lacks_is_not_counted(
    engine: Engine, practice: str, desired: dict[str, DesiredTable]
) -> None:
    counts = _apply(engine, practice, {**desired, "not_migrated_yet": DesiredTable()})

    assert counts.applied == len(desired)
    assert counts.altered == 0


def test_the_differences_are_reported_without_changing_anything(
    engine: Engine, practice: str, desired: dict[str, DesiredTable]
) -> None:
    with OrmSession(engine) as session:
        assert tenant_rls_differences(session, practice, desired, leave=_LEAVE) == {}

    _ddl(engine, f'DROP POLICY rls_patient_access ON "{practice}".patients')
    with OrmSession(engine) as session:
        found = tenant_rls_differences(session, practice, desired, leave=_LEAVE)
        session.commit()

    assert list(found) == ["patients"]
    assert any("CREATE POLICY rls_patient_access" in statement for statement in found["patients"])
    assert ("patients", "rls_patient_access") not in _policies(engine, practice)
