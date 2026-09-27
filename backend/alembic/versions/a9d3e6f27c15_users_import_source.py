# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""users.import_source and users.import_prompted_at

The onboarding answer to "are you importing from another records system?"
and when it was asked. Asked once; a Skip stamps the time and leaves the
source null, so the wizard never asks again and the import screen knows what
kind of archive to expect.

``platform.users`` columns are added from this chain like the other
onboarding stamps (``profile_basics_completed_at``, ``onboarding_state``),
idempotently, because the ``create_all`` bootstrap creates ORM columns first.

Revision ID: a9d3e6f27c15
Revises: f4b8c2d91a37
Create Date: 2026-09-27
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "a9d3e6f27c15"
down_revision: str | Sequence[str] | None = "f4b8c2d91a37"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("ALTER TABLE platform.users ADD COLUMN IF NOT EXISTS import_source VARCHAR(32) NULL")
    op.execute(
        "ALTER TABLE platform.users ADD COLUMN IF NOT EXISTS import_prompted_at TIMESTAMPTZ NULL"
    )


def downgrade() -> None:
    op.drop_column("users", "import_prompted_at", schema="platform")
    op.drop_column("users", "import_source", schema="platform")
