# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""tasks: let a walk back past e3a8c5f1d702 drop the column the purge reads

The hard purge reads and deletes a soft-deleted client's tasks through
``rls_patient_purge_read`` and ``rls_patient_purge_delete`` on ``tasks``. Both
compare ``tasks.patient_id``, so Postgres records them as depending on that
column. ``e3a8c5f1d702``'s downgrade drops ``tasks.patient_id`` and was written
before any policy read it, so with the policies in place it raises
``DependentObjectsStillExist`` and the whole downgrade fails.

That revision has landed, so it is not edited. This one sits at the head, so
every downgrade below it runs this downgrade first, and it drops the two
policies before anything reaches the column.

The upgrade does nothing. A revision does not create policies:
``reconcile_tenant_rls`` runs after every migrate and builds them from
``PATIENT_PURGE_TABLES``, as provisioning does, so the next migrate puts back
whatever a walk back took away.

Revision ID: 16d6543b3c6f
Revises: d5a2c8e41b70
Create Date: 2026-11-17
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "16d6543b3c6f"
down_revision: str | Sequence[str] | None = "d5a2c8e41b70"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    # search_path is set to the tenant schema by the runner.
    op.execute("DROP POLICY IF EXISTS rls_patient_purge_read ON tasks")
    op.execute("DROP POLICY IF EXISTS rls_patient_purge_delete ON tasks")
