# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""security guide acknowledgment columns on platform.users (THERAPY-i8sy.4)

Adds ``security_guide_acknowledged_at TIMESTAMPTZ NULL`` and
``security_guide_version VARCHAR(20) NULL`` to ``platform.users``.

The security & privacy guide for clinicians is shipped with the
frontend (or its equivalent in a downstream deployment). Clinicians
acknowledge a specific version of it during onboarding before they
get into the dashboard. The user row records the timestamp + version
pair — that's the audit trail; we deliberately do not store the full
guide text on the row (unlike BAA, which is a contract: the guide is
training material whose canonical copy lives in the repo).

NULL is meaningful in both columns: it's the "needs to acknowledge"
signal that the onboarding wizard keys off. No backfill — existing
users will be prompted on next login.

Deployments that do not bundle a guide leave gating on this field to
operator policy (similar to the existing BAA gate, which is disabled
by default).

Revision ID: d5a3e9b71f6c
Revises: c2e8a1f5d4b9
Create Date: 2026-05-14
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "d5a3e9b71f6c"
down_revision: str | Sequence[str] | None = "c2e8a1f5d4b9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE platform.users "
        "ADD COLUMN IF NOT EXISTS security_guide_acknowledged_at "
        "TIMESTAMPTZ NULL"
    )
    op.execute(
        "ALTER TABLE platform.users "
        "ADD COLUMN IF NOT EXISTS security_guide_version VARCHAR(20) NULL"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE platform.users DROP COLUMN IF EXISTS security_guide_version")
    op.execute("ALTER TABLE platform.users DROP COLUMN IF EXISTS security_guide_acknowledged_at")
