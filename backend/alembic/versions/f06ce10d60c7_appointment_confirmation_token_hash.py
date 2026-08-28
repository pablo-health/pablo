# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""appointments.confirmation_token_hash — the hold's redemption secret, hashed

A booking link that requires email confirmation creates a PENDING
appointment and mails the booker a one-time link back. The token in
that link is never stored; only its SHA-256 digest lives here, the
same hash-at-rest pattern ``LaunchIntentStore`` uses for its own
single-use ids. NULL for every appointment that never went through the
hold-and-confirm path.

Revision ID: f06ce10d60c7
Revises: e8c54fe3d577
Create Date: 2026-08-28
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "f06ce10d60c7"
down_revision: str | Sequence[str] | None = "e8c54fe3d577"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE appointments ADD COLUMN IF NOT EXISTS confirmation_token_hash VARCHAR(64) NULL"
    )
    # The confirm endpoint looks up a hold by hash; partial so it costs
    # nothing on the confirmed rows that are almost all of the table.
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_appointments_confirmation_token_hash "
        "ON appointments (confirmation_token_hash) "
        "WHERE confirmation_token_hash IS NOT NULL"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_appointments_confirmation_token_hash")
    op.execute("ALTER TABLE appointments DROP COLUMN IF EXISTS confirmation_token_hash")
