# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""platform.practice_domains: a host being taken down, and why a host is in error

Where a deployment serves practice hosts itself, removing a host is no longer
one DELETE: its routing and certificate are taken down first, by the domain
reconciler job, and the row goes last. Until then the row is ``removing``, so
the status CHECK gains that value.

``last_error`` records why a host is not served (a record it is waiting for, a
certificate that could not be issued, a record that stopped pointing here), for
whoever runs the deployment. ``cert_reissued_at`` is when the job last deleted
and requested a certificate again after a failed authorisation, which bounds
how often it does.

No PHI: hostnames and setup state.

Revision ID: c5f1d7a93e28
Revises: b3e91d5a7c24
Create Date: 2026-10-01
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "c5f1d7a93e28"
down_revision: str | Sequence[str] | None = "b3e91d5a7c24"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE platform.practice_domains
            ADD COLUMN IF NOT EXISTS last_error VARCHAR(500);
        ALTER TABLE platform.practice_domains
            ADD COLUMN IF NOT EXISTS cert_reissued_at TIMESTAMP WITH TIME ZONE;
        """
    )
    # Only where the CHECK lacks the value: on a fresh install the baseline
    # already built it, and rebuilding it here would change how it is rendered
    # in the captured template for nothing.
    op.execute(
        """
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM pg_constraint
                WHERE conname = 'practice_domains_status_check'
                  AND conrelid = 'platform.practice_domains'::regclass
                  AND pg_get_constraintdef(oid) LIKE '%removing%'
            ) THEN
                ALTER TABLE platform.practice_domains
                    DROP CONSTRAINT IF EXISTS practice_domains_status_check;
                ALTER TABLE platform.practice_domains
                    ADD CONSTRAINT practice_domains_status_check
                    CHECK (status IN ('pending', 'verifying', 'active', 'error', 'removing'));
            END IF;
        END $$;
        """
    )


def downgrade() -> None:
    # A host still being taken down has nowhere to go in the older vocabulary;
    # ``error`` keeps it visible rather than dropping a row whose serving may
    # still exist.
    op.execute(
        """
        UPDATE platform.practice_domains SET status = 'error' WHERE status = 'removing';
        ALTER TABLE platform.practice_domains
            DROP CONSTRAINT IF EXISTS practice_domains_status_check;
        ALTER TABLE platform.practice_domains
            ADD CONSTRAINT practice_domains_status_check
            CHECK (status IN ('pending', 'verifying', 'active', 'error'));
        """
    )
    op.execute(
        """
        ALTER TABLE platform.practice_domains DROP COLUMN IF EXISTS cert_reissued_at;
        ALTER TABLE platform.practice_domains DROP COLUMN IF EXISTS last_error;
        """
    )
