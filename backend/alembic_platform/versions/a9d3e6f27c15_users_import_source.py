# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""users.import_source and users.import_prompted_at

The onboarding answer to "are you importing from another records system?"
and when it was asked. Asked once; a Skip stamps the time and leaves the
source null, so the wizard never asks again and the import screen knows what
kind of archive to expect.

Platform chain: ``platform.users`` is owned here (see
``scripts/regen_platform_schema.py``). The older onboarding stamps on this
table were added from the tenant chain before this chain existed; new
columns come from here. Idempotent, like every revision in the chain.

Revision ID: a9d3e6f27c15
Revises: d1c7b94e3a26
Create Date: 2026-09-27
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "a9d3e6f27c15"
down_revision: str | Sequence[str] | None = "d1c7b94e3a26"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("ALTER TABLE platform.users ADD COLUMN IF NOT EXISTS import_source VARCHAR(32)")
    op.execute(
        "ALTER TABLE platform.users ADD COLUMN IF NOT EXISTS import_prompted_at "
        "TIMESTAMP WITH TIME ZONE"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE platform.users DROP COLUMN IF EXISTS import_prompted_at;")
    op.execute("ALTER TABLE platform.users DROP COLUMN IF EXISTS import_source;")
