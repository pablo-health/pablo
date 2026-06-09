# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Drop the dead per-tenant ``users`` / ``user_preferences`` tables.

The very first practice-schema migration (``d20c4753ded3``) created
``users`` and ``user_preferences`` inside every practice schema — a
leftover from when user accounts and preferences were per-tenant. They
have since moved to the shared platform schema: the ORM models
``PlatformUserRow`` / ``PlatformUserPreferencesRow`` carry
``__table_args__ = {"schema": "platform"}``, ``platform_metadata.create_all``
provisions ``platform.users`` / ``platform.user_preferences``, and every
caller resolves through those schema-qualified models. The per-tenant
copies are never read or written, nothing foreign-keys to them, and RLS /
provisioning don't touch them. Drop them so a tenant schema holds only the
tables it actually uses.

Scope. Per-tenant only — ``platform.users`` and ``platform.user_preferences``
(the live tables) are untouched. The drop is qualified to
``current_schema()`` rather than left unqualified: under the per-tenant
fan-out the search_path is ``<tenant>, platform, public``, so an
unqualified ``DROP TABLE`` would fall through to ``platform`` once the
tenant copy is gone (an idempotent re-run) and delete the live table.
Qualifying to ``current_schema()`` — which is the tenant or the
``practice`` template, never ``platform`` (see ``alembic/env.py``) — plus
the explicit ``<> 'platform'`` guard makes that impossible.

Idempotent under the fan-out via ``DROP TABLE IF EXISTS``.

Revision ID: a3f8d21c5e90
Revises: c1d7e4a9f2b6
Create Date: 2026-06-08
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

if TYPE_CHECKING:
    from collections.abc import Sequence

__all__ = ["branch_labels", "depends_on", "down_revision", "revision"]

revision: str = "a3f8d21c5e90"
down_revision: str | Sequence[str] | None = "c1d7e4a9f2b6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Dead per-tenant tables, in drop order (neither references the other).
_DEAD_TABLES = ("user_preferences", "users")


def upgrade() -> None:
    for table in _DEAD_TABLES:
        op.execute(
            f"""DO $$
            BEGIN
                -- Drop only the per-tenant / template copy, never platform's.
                IF current_schema() <> 'platform' THEN
                    EXECUTE format('DROP TABLE IF EXISTS %I.{table}', current_schema());
                END IF;
            END $$;"""
        )


def downgrade() -> None:
    # Recreate the tables as ``d20c4753ded3`` originally defined them in the
    # practice schema (string user ids — they predate the uuid conversion).
    # Guarded so the platform copies are never shadowed.
    if op.get_bind().exec_driver_sql("SELECT current_schema()").scalar() == "platform":
        return
    op.create_table(
        "users",
        sa.Column("id", sa.String(length=128), nullable=False),
        sa.Column("email", sa.String(length=255), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("created_at", sa.String(length=30), nullable=False),
        sa.Column("title", sa.String(length=50), nullable=True),
        sa.Column("credentials", sa.String(length=100), nullable=True),
        sa.Column("picture", sa.Text(), nullable=True),
        sa.Column("baa_accepted_at", sa.String(length=30), nullable=True),
        sa.Column("baa_version", sa.String(length=10), nullable=True),
        sa.Column("baa_legal_name", sa.String(length=255), nullable=True),
        sa.Column("baa_license_number", sa.String(length=100), nullable=True),
        sa.Column("baa_license_state", sa.String(length=2), nullable=True),
        sa.Column("baa_practice_name", sa.String(length=255), nullable=True),
        sa.Column("baa_business_address", sa.String(length=500), nullable=True),
        sa.Column("baa_full_text", sa.Text(), nullable=True),
        sa.Column("is_admin", sa.Boolean(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("mfa_enrolled_at", sa.String(length=30), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_users_email"), "users", ["email"], unique=False)
    op.create_table(
        "user_preferences",
        sa.Column("user_id", sa.String(length=128), nullable=False),
        sa.Column("preferences", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.PrimaryKeyConstraint("user_id"),
    )
