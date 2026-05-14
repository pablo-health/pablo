"""user_identities: decouple Pablo user_id from auth provider subject

Adds ``platform.user_identities`` — a (provider, subject_id) -> user_id
mapping so the storage identity is no longer pinned to a single auth
provider's UID. Backfills the table for every existing platform.users
row by linking them as `('firebase', <existing id>, <same id>)` so
legacy FK references continue to resolve unchanged.

All DDL is idempotent so per-tenant fan-out (which replays every
migration against every tenant connection) does not error on subsequent
runs against the shared platform schema.

Revision ID: a4c91b6e3f08
Revises: 65fdccbd8f77
Create Date: 2026-05-14
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "a4c91b6e3f08"
down_revision: str | Sequence[str] | None = "65fdccbd8f77"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS platform.user_identities (
            provider VARCHAR(32) NOT NULL,
            subject_id VARCHAR(64) NOT NULL,
            user_id VARCHAR(128) NOT NULL,
            linked_at TIMESTAMP WITH TIME ZONE NOT NULL,
            CONSTRAINT user_identities_pkey PRIMARY KEY (provider, subject_id)
        );
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_user_identities_user_id"
        " ON platform.user_identities (user_id);"
    )

    # Backfill: every existing user gets a firebase->itself mapping so
    # downstream FK columns (which currently hold Firebase uids) continue
    # to resolve. ON CONFLICT makes the backfill safe under per-tenant
    # fan-out, which replays this migration against every tenant.
    op.execute(
        """
        INSERT INTO platform.user_identities (provider, subject_id, user_id, linked_at)
        SELECT 'firebase', id, id, COALESCE(created_at, NOW())
        FROM platform.users
        ON CONFLICT (provider, subject_id) DO NOTHING;
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS platform.ix_user_identities_user_id;")
    op.execute("DROP TABLE IF EXISTS platform.user_identities;")
