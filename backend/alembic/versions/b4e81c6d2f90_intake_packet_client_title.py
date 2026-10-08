# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""A title for a packet that the person filling it in sees

``client_title`` is what the portal names a packet on a person's list.
The packet's ``name`` is the practice's own label, written for its list
("New client intake 2026-10-07") and never shown to the person filling it
in, so a person with two packets saw two rows that read the same.

Nullable, and no existing row is touched: a packet with no title keeps the
wording the portal has always used. Idempotent, like every revision in
this chain.

Revision ID: b4e81c6d2f90
Revises: b3e9c6d4f217
Create Date: 2026-10-08
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "b4e81c6d2f90"
down_revision: str | Sequence[str] | None = "b3e9c6d4f217"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE intake_packet_templates ADD COLUMN IF NOT EXISTS client_title VARCHAR(120);"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE intake_packet_templates DROP COLUMN IF EXISTS client_title;")
