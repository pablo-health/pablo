# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""add provider_type column to platform.users (THERAPY-iz5)

Adds ``provider_type VARCHAR(32) NULL`` to ``platform.users``. The
column is the per-user signal that downstream SOAP-pipeline plugins
(template selection, prescriber prompt, etc.) key off. Possible values
are constrained at the application layer to ``"therapist"``,
``"prescriber"``, ``"both"``; the column stays a plain VARCHAR so the
set can grow without a follow-up migration.

NULL is meaningful: it's the "user has not picked yet → run the
onboarding flow" signal. Downstream onboarding flows redirect to
``/onboarding/provider-type`` while this column is NULL. We
deliberately do not backfill a default for existing rows — they will
be prompted on next login.

Revision ID: c2e8a1f5d4b9
Revises: 65fdccbd8f77
Create Date: 2026-05-14
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "c2e8a1f5d4b9"
down_revision: str | Sequence[str] | None = "65fdccbd8f77"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("ALTER TABLE platform.users ADD COLUMN IF NOT EXISTS provider_type VARCHAR(32) NULL")


def downgrade() -> None:
    op.execute("ALTER TABLE platform.users DROP COLUMN IF EXISTS provider_type")
