# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""platform.practice_domains: the hosts a practice serves its portal and site from

One row per hostname. The hostname is the primary key, so a host belongs to
exactly one practice; that is also why the table is in the shared schema — a
request is matched to its practice by host before any tenant is known.

A practice may hold several hosts per ``purpose`` (``portal`` or ``site``),
with at most one primary per purpose, enforced by a partial unique index.

**Two starting points, one end state.** Some installations already have this
table without ``purpose``, ``is_primary`` or ``verified_at`` — it was created
earlier, outside this chain, for websites only. So the upgrade creates the
table if it is missing, then adds each new column, constraint and index only
if it is missing. On a fresh install the baseline has already built the table
at its current shape and every statement here is a no-op, the same as
``d1c7b94e3a26_portal_practice_slugs``. Existing rows become websites, because
that is all they could have been.

The downgrade removes what this revision adds and leaves the table, since on
those installations the table predates it.

No PHI: public hostnames and their setup state.

Revision ID: f8c2a61d4e97
Revises: d4a7e2c91b06
Create Date: 2026-10-01
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "f8c2a61d4e97"
down_revision: str | Sequence[str] | None = "d4a7e2c91b06"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS platform.practice_domains (
            domain           VARCHAR(255) NOT NULL PRIMARY KEY,
            practice_id      VARCHAR(128) NOT NULL,
            purpose          VARCHAR(10)  NOT NULL DEFAULT 'site',
            kind             VARCHAR(16)  NOT NULL,
            status           VARCHAR(20)  NOT NULL DEFAULT 'pending',
            is_primary       BOOLEAN      NOT NULL DEFAULT false,
            verified_at      TIMESTAMP WITH TIME ZONE,
            cert_status      VARCHAR(20),
            dns_auth_record  TEXT,
            created_at       TIMESTAMP WITH TIME ZONE NOT NULL,
            updated_at       TIMESTAMP WITH TIME ZONE,
            CONSTRAINT practice_domains_kind_check
                CHECK (kind IN ('subdomain', 'vanity')),
            CONSTRAINT practice_domains_status_check
                CHECK (status IN ('pending', 'verifying', 'active', 'error')),
            CONSTRAINT practice_domains_purpose_check
                CHECK (purpose IN ('portal', 'site'))
        );
        """
    )
    op.execute(
        """
        ALTER TABLE platform.practice_domains
            ADD COLUMN IF NOT EXISTS purpose VARCHAR(10) NOT NULL DEFAULT 'site',
            ADD COLUMN IF NOT EXISTS is_primary BOOLEAN NOT NULL DEFAULT false,
            ADD COLUMN IF NOT EXISTS verified_at TIMESTAMP WITH TIME ZONE;
        """
    )
    # ADD CONSTRAINT has no IF NOT EXISTS.
    op.execute(
        """
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM pg_constraint
                WHERE conname = 'practice_domains_purpose_check'
                  AND conrelid = 'platform.practice_domains'::regclass
            ) THEN
                ALTER TABLE platform.practice_domains
                    ADD CONSTRAINT practice_domains_purpose_check
                    CHECK (purpose IN ('portal', 'site'));
            END IF;
        END
        $$;
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_practice_domains_practice_id
            ON platform.practice_domains (practice_id);
        """
    )
    op.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS uq_practice_domains_primary
            ON platform.practice_domains (practice_id, purpose)
            WHERE is_primary;
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS platform.uq_practice_domains_primary;")
    op.execute(
        """
        ALTER TABLE platform.practice_domains
            DROP CONSTRAINT IF EXISTS practice_domains_purpose_check,
            DROP COLUMN IF EXISTS verified_at,
            DROP COLUMN IF EXISTS is_primary,
            DROP COLUMN IF EXISTS purpose;
        """
    )
