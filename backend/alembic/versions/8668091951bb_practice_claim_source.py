# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""What a claim needs for any session in the practice

Adds ``practice_claim_source(appointment_id)``, the third function the
practice directory role owns (``app.db.practice_directory``): one visit's
claim fields, whoever held the session. Created here and so captured by the
template; owned, granted and policied by
``apply_practice_directory_access``, which now also gives the role its read
policy on ``patient_coverage``, ``appointment_types`` and
``clinician_profiles``.

Additive only: a function, column grants and policies naming the directory
role alone, so every other role reads exactly what it did. Idempotent, like
every revision in this chain. Downgrade drops the function as its owner, the
three new policies, and the grants only this function needed.

Revision ID: 8668091951bb
Revises: b8e3f1a6d924
Create Date: 2026-11-26
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from alembic import op
from sqlalchemy import text

if TYPE_CHECKING:
    from collections.abc import Sequence

    from sqlalchemy.engine import Connection

__all__ = ["branch_labels", "depends_on", "down_revision", "revision"]

revision: str = "8668091951bb"
down_revision: str | Sequence[str] | None = "b8e3f1a6d924"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: The tables only this function reads.
_NEW_TABLES = ("patient_coverage", "appointment_types", "clinician_profiles")
#: Columns on tables the other two functions read too, which only this one needs.
_NEW_COLUMNS = {
    "appointments": (
        "user_id, patient_id, appointment_type_id, start_at, service_code, unit_count, "
        "modifiers, diagnosis_codes, place_of_service, video_link"
    ),
    "patients": "sex, address_line1, address_line2, city, state, postal_code, phone, rate_cents",
}


def _schema(bind: Connection) -> str:
    """The schema this pass is migrating. A tenant revision always runs in one."""
    from app.db import _validate_schema_name  # noqa: PLC0415

    schema = bind.execute(text("SELECT current_schema()")).scalar()
    if not schema:
        msg = "practice_claim_source: no current schema to migrate"
        raise RuntimeError(msg)
    _validate_schema_name(schema)
    return str(schema)


def upgrade() -> None:
    # Imported here, not at module level: revision walkers import every
    # migration without env.py's sys.path setup (see e2b7c4f19d38).
    from app.db import DEFAULT_PRACTICE_SCHEMA  # noqa: PLC0415
    from app.db.practice_directory import (  # noqa: PLC0415
        apply_practice_directory_access,
        create_claim_source_function,
    )

    bind = op.get_bind()
    schema = _schema(bind)
    create_claim_source_function(bind, schema)
    if schema != DEFAULT_PRACTICE_SCHEMA:
        # The template schema carries no policies and nothing reads it.
        apply_practice_directory_access(bind, schema)


def downgrade() -> None:
    from app.db.practice_directory import (  # noqa: PLC0415
        DIRECTORY_ROLE,
        drop_claim_source_function,
    )

    bind = op.get_bind()
    schema = _schema(bind)
    drop_claim_source_function(bind, schema)
    role_exists = bind.execute(
        text("SELECT 1 FROM pg_roles WHERE rolname = :role"), {"role": DIRECTORY_ROLE}
    ).scalar()
    for table in _NEW_TABLES:
        op.execute(f"DROP POLICY IF EXISTS rls_practice_directory_read ON {table}")
        if role_exists:
            op.execute(f"REVOKE SELECT ON {table} FROM {DIRECTORY_ROLE}")  # noqa: S608 — constants
    if role_exists:
        for table, columns in _NEW_COLUMNS.items():
            op.execute(f"REVOKE SELECT ({columns}) ON {table} FROM {DIRECTORY_ROLE}")  # noqa: S608 — constants
