# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""platform.practice_invites: an invitation to join a practice

A practice's owner or an admin invites a clinician by address; the account
that redeems the link joins the practice (``app.services.practice_invites``).
Only the sha256 of the link's token is stored. An invitation is open until it
is redeemed, revoked or expired, and is never reopened.

Idempotent, like the rest of this chain.

No PHI: ids, times, a hash, and a clinician's work address.

Revision ID: b8e3f1c45d29
Revises: e4b9c2d71a63
Create Date: 2026-11-21
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "b8e3f1c45d29"
down_revision: str | Sequence[str] | None = "e4b9c2d71a63"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS platform.practice_invites (
            id                   UUID NOT NULL,
            practice_id          VARCHAR(128) NOT NULL,
            email                TEXT NOT NULL,
            token_hash           VARCHAR(64) NOT NULL,
            invited_by           UUID,
            created_at           TIMESTAMP WITH TIME ZONE NOT NULL,
            expires_at           TIMESTAMP WITH TIME ZONE NOT NULL,
            redeemed_at          TIMESTAMP WITH TIME ZONE,
            redeemed_by_user_id  UUID,
            revoked_at           TIMESTAMP WITH TIME ZONE,
            CONSTRAINT practice_invites_pkey PRIMARY KEY (id),
            CONSTRAINT practice_invites_token_hash_key UNIQUE (token_hash),
            CONSTRAINT practice_invites_practice_id_fkey FOREIGN KEY (practice_id)
                REFERENCES platform.practices (id) ON DELETE CASCADE,
            CONSTRAINT practice_invites_invited_by_fkey FOREIGN KEY (invited_by)
                REFERENCES platform.users (id) ON DELETE SET NULL,
            CONSTRAINT practice_invites_redeemed_by_user_id_fkey FOREIGN KEY (redeemed_by_user_id)
                REFERENCES platform.users (id) ON DELETE SET NULL
        );
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_practice_invites_practice_id "
        "ON platform.practice_invites (practice_id)"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS platform.practice_invites")
