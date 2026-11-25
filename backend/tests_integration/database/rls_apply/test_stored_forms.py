# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Stored policy forms, fingerprints, and named roles."""

from __future__ import annotations

import os
import uuid
from typing import TYPE_CHECKING

import pytest
from app.db.rls_apply import (
    APPLIED_PREFIX,
    DesiredTable,
)
from sqlalchemy import text

if TYPE_CHECKING:
    from sqlalchemy.engine import Engine

from .helpers import (
    _DIRECTORY_POLICY,
    _TEMPLATE,
    _TREATING,
    _apply,
    _ddl,
    _drop_schema,
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


def test_the_directory_policys_name_protects_nothing_on_another_table(
    engine: Engine, practice: str, desired: dict[str, DesiredTable]
) -> None:
    """The file puts that policy on three tables; anywhere else it is a stray."""
    _ddl(
        engine,
        f'CREATE POLICY {_DIRECTORY_POLICY} ON "{practice}".therapy_sessions USING (true)',
    )

    counts = _apply(engine, practice, desired)

    assert counts.altered == 1
    assert ("therapy_sessions", _DIRECTORY_POLICY) not in _policies(engine, practice)
    assert ("patients", _DIRECTORY_POLICY) in _policies(engine, practice)


def test_an_edit_to_a_policy_the_applier_itself_recorded_is_put_back(
    engine: Engine, desired: dict[str, DesiredTable]
) -> None:
    """The recorded hash covers what was stored, so an edit under it does not match.

    Built from the file alone, so that some policies carry the applier's own
    comment after the first pass; ``patients`` in the test above never does.
    """
    from app.db.provisioning import (  # noqa: PLC0415
        TEMPLATE_ROW_SECURITY_MARKER,
        apply_template_sql,
    )

    schema = f"practice_test_apply_edit_{uuid.uuid4().hex[:8]}"
    body, _, row_security = _TEMPLATE.read_text().partition(TEMPLATE_ROW_SECURITY_MARKER)
    apply_template_sql(engine, schema, body)
    apply_template_sql(engine, schema, row_security)
    try:
        assert _apply(engine, schema, desired).altered == 0
        with engine.connect() as conn:
            table, policy, command = conn.execute(
                text(
                    "SELECT c.relname, p.polname, p.polcmd::text FROM pg_policy p "
                    "JOIN pg_class c ON c.oid = p.polrelid "
                    "JOIN pg_namespace n ON n.oid = c.relnamespace "
                    "WHERE n.nspname = :s "
                    "AND obj_description(p.oid, 'pg_policy') LIKE :prefix "
                    "ORDER BY 1, 2 LIMIT 1"
                ),
                {"s": schema, "prefix": APPLIED_PREFIX + "%"},
            ).one()
        settled = _policies(engine, schema)

        clause = "WITH CHECK" if command == "a" else "USING"
        _ddl(engine, f'ALTER POLICY "{policy}" ON "{schema}"."{table}" {clause} (true)')
        assert _policies(engine, schema) != settled, "the damage was not done"

        assert _apply(engine, schema, desired).altered == 1
        assert _policies(engine, schema) == settled
    finally:
        _drop_schema(engine, schema)


def test_a_restrictive_policy_for_a_named_role_settles(
    engine: Engine, practice: str, desired: dict[str, DesiredTable]
) -> None:
    """The template has no such policy today; the applier must still not loop on one."""
    with engine.connect() as conn:
        role = conn.execute(text("SELECT current_user")).scalar_one()
    patients = desired["patients"]
    extended = {
        **desired,
        "patients": DesiredTable(
            enabled=patients.enabled,
            forced=patients.forced,
            policies={
                **patients.policies,
                "rls_narrow": (
                    "CREATE POLICY rls_narrow ON __TENANT_SCHEMA__.patients "
                    f"AS RESTRICTIVE FOR SELECT TO {role} USING (true);"
                ),
            },
            comments=dict(patients.comments),
        ),
    }

    assert _apply(engine, practice, extended).altered == 1
    assert _policies(engine, practice)["patients", "rls_narrow"][:2] == ("RESTRICTIVE", "SELECT")
    assert _patients_read_by(engine, practice, _TREATING) == 2

    oids = _policy_oids(engine, practice)
    assert _apply(engine, practice, extended).altered == 0
    assert _policy_oids(engine, practice) == oids
