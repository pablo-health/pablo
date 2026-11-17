# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""platform.practice_members: the practice an account belongs to

One row per account (``user_id`` is the primary key), naming the practice it
belongs to. Recorded only for now: a signed-in request still resolves its
practice through the email mapping, and compares that answer with this row.
A first sign-in through the mapping writes the row (``joined_via =
'email_claim'``, ``claimed_email`` the mapped address).

The table starts empty. Accounts that are already mapped are given a row by
the migrate step (``app.db.practice_members.backfill``), which runs after
every upgrade and is idempotent, rather than by this revision: a fresh install
builds the platform schema from the template and never runs this file, and
the backfill has to cover both.

Idempotent, like the rest of this chain.

No PHI: account and practice ids, a time, how the row was written, and the
address the account signs in with.

Revision ID: c5d1e8a27f43
Revises: a3f8c1d6e924
Create Date: 2026-11-17
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "c5d1e8a27f43"
down_revision: str | Sequence[str] | None = "a3f8c1d6e924"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS platform.practice_members (
            user_id        UUID NOT NULL,
            practice_id    VARCHAR(128) NOT NULL,
            joined_at      TIMESTAMP WITH TIME ZONE NOT NULL,
            joined_via     VARCHAR(16) NOT NULL,
            invited_by     UUID,
            claimed_email  TEXT,
            CONSTRAINT practice_members_pkey PRIMARY KEY (user_id),
            CONSTRAINT practice_members_user_id_fkey FOREIGN KEY (user_id)
                REFERENCES platform.users (id) ON DELETE CASCADE,
            CONSTRAINT practice_members_practice_id_fkey FOREIGN KEY (practice_id)
                REFERENCES platform.practices (id) ON DELETE CASCADE,
            CONSTRAINT practice_members_invited_by_fkey FOREIGN KEY (invited_by)
                REFERENCES platform.users (id) ON DELETE SET NULL,
            CONSTRAINT practice_members_joined_via_check
                CHECK (joined_via IN ('email_claim', 'invite', 'backfill'))
        );
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_practice_members_practice_id "
        "ON platform.practice_members (practice_id)"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS platform.practice_members")
