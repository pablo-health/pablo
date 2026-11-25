# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Helpers for single-practice conversion."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from sqlalchemy import text
from sqlalchemy.orm import Session

if TYPE_CHECKING:
    from app.db.single_practice_migration import OrphanCount
    from sqlalchemy.engine import Connection, Engine


def _counts(counts: list[OrphanCount], table: str) -> OrphanCount:

    for c in counts:
        if c.table == table:
            return c
    raise AssertionError(f"{table} not in pre-flight results: {[c.table for c in counts]}")


def _count(engine: Engine, schema: str, table: str) -> int:
    """Count rows with the schema on the search_path.

    After the migration the policies are live, and ``has_patient_access``'s body
    names ``patient_clinicians`` unqualified — so it resolves against the
    CALLER's search_path, not the function's. Reading a migrated schema without
    setting it raises ``relation "patient_clinicians" does not exist``, which is
    the same arrangement ``set_tenant_schema`` makes for every real request.

    Pass the superuser engine when the question is "is the row there", and the
    ordinary one when it is "can this principal see it".
    """
    from app.db import PLATFORM_SCHEMA  # noqa: PLC0415

    with engine.connect() as conn:
        conn.execute(text(f'SET search_path = "{schema}", {PLATFORM_SCHEMA}, public'))
        return conn.execute(text(f'SELECT count(*) FROM "{schema}"."{table}"')).scalar_one()  # noqa: S608


def _seed_patient(conn: Connection, schema: str, user_id: str, *, with_grant: bool = True) -> str:
    """A patient row, optionally with the grant that makes it readable.

    ``patients`` carries no ``user_id``: ownership lives entirely in
    ``patient_clinicians``. That is precisely why a missing grant is
    unrecoverable rather than merely inconvenient — there is no column left on
    the row that says whose chart it was.
    """

    patient_id = str(uuid.uuid4())
    conn.execute(
        text(
            f"INSERT INTO {schema}.patients (id, first_name, last_name, "  # noqa: S608 — schema/table are validated identifiers, never user input
            f"first_name_lower, last_name_lower, status, session_count, "
            f"created_at, updated_at) "
            f"VALUES (:p, 'Test', 'Patient', 'test', 'patient', 'active', 0, now(), now())"
        ),
        {"p": patient_id},
    )
    if with_grant:
        conn.execute(
            text(
                f"INSERT INTO {schema}.patient_clinicians "  # noqa: S608 — schema/table are validated identifiers, never user input
                f"(patient_id, user_id, role, granted_by) VALUES (:p, :u, 'primary', :u)"
            ),
            {"p": patient_id, "u": user_id},
        )
    return patient_id


def _seed_identity(conn: Connection, *, email: str, as_user: bool, with_mapping: bool) -> None:
    """An identity in the shape a pre-mapping deployment left behind.

    The user row goes in through the ORM rather than raw SQL: ``platform.users``
    has grown NOT NULL columns whose defaults are Python-side (``status``,
    ``chat_quality_review_opt_in``, …), so a hand-written INSERT has to be
    updated every time one lands. The ORM already knows them.
    """
    from app.db import DEFAULT_PRACTICE_ID, PLATFORM_SCHEMA  # noqa: PLC0415

    if as_user:
        from app.db.platform_models import PlatformUserRow  # noqa: PLC0415

        with Session(bind=conn) as session:
            session.add(
                PlatformUserRow(
                    id=str(uuid.uuid4()),
                    email=email,
                    name="Test User",
                    created_at=datetime.now(UTC),
                )
            )
            session.flush()
    else:
        conn.execute(
            text(
                f"INSERT INTO {PLATFORM_SCHEMA}.allowed_emails "  # noqa: S608 - PLATFORM_SCHEMA is a module constant, not input
                f"(email, practice_id, added_by, added_at) "
                f"VALUES (:e, NULL, 'test', now())"
            ),
            {"e": email},
        )
    if with_mapping:
        conn.execute(
            text(
                f"INSERT INTO {PLATFORM_SCHEMA}.email_tenant_mappings "  # noqa: S608 - PLATFORM_SCHEMA is a module constant, not input
                f"(email, tenant_id, practice_id, created_at) "
                f"VALUES (:e, :p, :p, now())"
            ),
            {"e": email, "p": DEFAULT_PRACTICE_ID},
        )


def _clear_identities(conn: Connection, emails: list[str]) -> None:
    from app.db import PLATFORM_SCHEMA  # noqa: PLC0415

    for table in ("email_tenant_mappings", "allowed_emails", "users"):
        conn.execute(
            text(f"DELETE FROM {PLATFORM_SCHEMA}.{table} WHERE email = ANY(:e)"),  # noqa: S608
            {"e": emails},
        )
