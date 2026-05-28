# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""practices.provisioning_status for async provisioning (THERAPY-da7t)

Adds ``platform.practices.provisioning_status VARCHAR(20) NOT NULL
DEFAULT 'ready'``. Tracks whether per-tenant schema DDL (the work
``provision_tenant`` does) has completed. Three values:

- ``'in_progress'`` — the marketing-signup endpoint reserved the
  tenant id and inserted the platform record, but the per-tenant
  schema DDL is still running in the background. Request handlers
  whose tenant resolves to this row return ``503`` so we don't try
  to query an empty schema.
- ``'ready'`` — provisioning finished; the tenant is open for
  business. This is the default for every pre-existing row (no
  reprovisioning needed; their DDL completed long ago under the
  prior synchronous flow).
- ``'failed'`` — the background provisioning task raised before
  completing. Operator intervention required; admin endpoints can
  inspect and retry.

Default ``'ready'`` is critical: any row already in the table at
migration time was provisioned the old synchronous way and is
already-ready by definition. Without the default, those rows would
get blocked on the new gate the moment this migration lands.

Revision ID: a4f7e2c81b9d
Revises: 7d2e9f4a1b38
Create Date: 2026-05-28
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "a4f7e2c81b9d"
down_revision: str | Sequence[str] | None = "7d2e9f4a1b38"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Both statements must be idempotent: platform.practices is a
    # cross-tenant table, but tenant migrations run the alembic chain
    # once per practice schema, so every per-tenant pass re-applies
    # the same DDL against the shared platform table. ADD COLUMN IF
    # NOT EXISTS handles the column directly; Postgres has no ADD
    # CONSTRAINT IF NOT EXISTS so the constraint is wrapped in a DO
    # block that swallows duplicate_object.
    op.execute(
        "ALTER TABLE platform.practices "
        "ADD COLUMN IF NOT EXISTS provisioning_status VARCHAR(20) "
        "NOT NULL DEFAULT 'ready'"
    )
    op.execute(
        "DO $$ BEGIN "
        "ALTER TABLE platform.practices "
        "ADD CONSTRAINT practices_provisioning_status_chk "
        "CHECK (provisioning_status IN ('in_progress', 'ready', 'failed')); "
        "EXCEPTION WHEN duplicate_object THEN NULL; "
        "END $$"
    )


def downgrade() -> None:
    op.execute(
        "ALTER TABLE platform.practices DROP CONSTRAINT IF EXISTS practices_provisioning_status_chk"
    )
    op.execute("ALTER TABLE platform.practices DROP COLUMN IF EXISTS provisioning_status")
