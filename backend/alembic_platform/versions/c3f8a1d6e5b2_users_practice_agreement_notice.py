# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""users.practice_agreement_notice_seen_at

When a clinician who does not own their practice was shown, once, that the
practice has a business associate agreement in force. Only the time is kept:
there is nothing to attest and no version to track.

Idempotent, like the rest of this chain.

Revision ID: c3f8a1d6e5b2
Revises: b8e3f1c45d29
Create Date: 2026-11-25
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "c3f8a1d6e5b2"
down_revision: str | Sequence[str] | None = "b8e3f1c45d29"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE platform.users ADD COLUMN IF NOT EXISTS practice_agreement_notice_seen_at "
        "TIMESTAMP WITH TIME ZONE"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE platform.users DROP COLUMN IF EXISTS practice_agreement_notice_seen_at")
