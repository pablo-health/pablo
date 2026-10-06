# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""psychotherapy_window on notes

The visit's psychotherapy window: where the draft proposed the therapy
portion began, and the start or minutes the clinician confirmed. It lives
beside the note's content rather than in it, so drafting the note again
keeps what the clinician confirmed. NULL for every note without one.

Additive only, no backfill. Idempotent: fanned out once per practice schema.

Revision ID: c3f9a1d6e2b8
Revises: b6c2e8f41a07
Create Date: 2026-10-06
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "c3f9a1d6e2b8"
down_revision: str | Sequence[str] | None = "b6c2e8f41a07"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("ALTER TABLE notes ADD COLUMN IF NOT EXISTS psychotherapy_window JSONB NULL")


def downgrade() -> None:
    op.execute("ALTER TABLE notes DROP COLUMN IF EXISTS psychotherapy_window")
