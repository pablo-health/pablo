# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""platform.practice_domains: when a host's records were all in place, and
whether its wait since then was reported

``records_complete_at`` is when a DNS check first found every record a host
needs, while the host was not active. It is cleared when one of them goes
missing or wrong, and when the host becomes active. A host that stays not
active long after it is set is shown as taking too long, so the clock starts
when the practice has done its part, never when the host was added.

``stuck_reported_at`` is when that was first reported, so it is reported once
per episode; it is cleared with ``records_complete_at``.

Existing rows start with both empty and pick ``records_complete_at`` up on
their next check.

No PHI: setup state.

Revision ID: e3b8c41f6a52
Revises: a9d3f6c21e87
Create Date: 2026-10-01
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "e3b8c41f6a52"
down_revision: str | Sequence[str] | None = "a9d3f6c21e87"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE platform.practice_domains
            ADD COLUMN IF NOT EXISTS records_complete_at TIMESTAMP WITH TIME ZONE;
        ALTER TABLE platform.practice_domains
            ADD COLUMN IF NOT EXISTS stuck_reported_at TIMESTAMP WITH TIME ZONE;
        """
    )


def downgrade() -> None:
    op.execute(
        """
        ALTER TABLE platform.practice_domains DROP COLUMN IF EXISTS stuck_reported_at;
        ALTER TABLE platform.practice_domains DROP COLUMN IF EXISTS records_complete_at;
        """
    )
