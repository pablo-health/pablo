# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""appointments: remember which time and link the client was last sent

Adds ``appointments.client_link_notice``, nullable, a 64-character digest of
the start time and the video link the client was last sent. A save that
leaves both as they were finds the same digest and sends nothing; a new time
or a new link makes a different one. ``NULL`` means nothing has been sent.

No backfill. Existing appointments were never sent a link, and sending one
now for each would be a burst of mail nobody asked for; the first change
after this lands sends.

Idempotent, because an earlier revision's downgrade-and-replay can run this
one again over a schema that has it.

Revision ID: d5a2c8e41b70
Revises: c3e7a91f5d20
Create Date: 2026-11-10
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "d5a2c8e41b70"
down_revision: str | Sequence[str] | None = "c3e7a91f5d20"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("ALTER TABLE appointments ADD COLUMN IF NOT EXISTS client_link_notice VARCHAR(64)")


def downgrade() -> None:
    op.execute("ALTER TABLE appointments DROP COLUMN IF EXISTS client_link_notice")
