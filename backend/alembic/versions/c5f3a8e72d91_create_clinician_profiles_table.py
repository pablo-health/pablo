# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""create clinician_profiles table

The ``clinician_profiles`` table was historically created by
``Base.metadata.create_all`` at provisioning time and never had a
migration of its own. The tenant template regen
(``backend/scripts/regen_tenant_template.py``) runs pure alembic, so
the table was absent from ``tenant_template.sql`` — fresh tenants
provisioned via the template path (notably the pentest fixture path)
had no ``clinician_profiles`` table at all. Once ``GET /me/status``
started querying it, dashboards for those tenants 500'd.

The DDL is wrapped in ``IF NOT EXISTS`` so it's a no-op on every
tenant that already has the table (legacy schemas + the manual
backfill applied to dev/prod tenants on 2026-05-24).

Revision ID: c5f3a8e72d91
Revises: a1c4f7e9b302
Create Date: 2026-05-24
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "c5f3a8e72d91"
down_revision: str | Sequence[str] | None = "a1c4f7e9b302"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS clinician_profiles (
          user_id VARCHAR(128) PRIMARY KEY,
          practice_id VARCHAR(128) NOT NULL,
          title VARCHAR(50),
          credentials VARCHAR(100),
          role VARCHAR(20) NOT NULL DEFAULT 'clinician',
          joined_at TIMESTAMPTZ NOT NULL
        )
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS clinician_profiles")
