# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Re-run enable_rls_on_schema per tenant — heal drifted RLS state.

``enable_rls_on_schema`` runs at provisioning time, so a tenant's RLS
state reflects whatever the code said *when that tenant was created*.
Policy arms added later (``compliance_documents``'s
``uploaded_by_user_id`` shape) and tables later declared not-row-scoped
(``ehr_routes``, the vestigial per-tenant ``users``) never reached
pre-existing schemas. The failure mode is severe and silent: a table
with ``FORCE ROW LEVEL SECURITY`` and no policy is deny-all under a
NOBYPASSRLS role, so every read returns zero rows and every write
raises ``InsufficientPrivilege`` — observed in the wild as the entire
compliance-document upload surface failing with a 500 on any tenant
provisioned before the policy arm existed.

The function is explicitly written to be idempotent and self-healing
(DROP POLICY IF EXISTS before each CREATE; not-row-scoped tables get
RLS disabled each run), so the migration simply invokes it against the
current schema and lets it true everything up to the current shape.
Calling app code from a migration is deliberate here: the desired
semantics are "make this schema match what provisioning would build
today", not a frozen snapshot — exactly what re-running provisioning's
own function provides. Deployments that register additional
not-row-scoped tenant tables via ``register_overlay_not_row_scoped``
must do so in their migration entrypoint (not only the serving
process) so this heal sees the same registry provisioning does.

The per-tenant fan-out applies this to every tenant schema; the
deploy-time default path is a no-op (``enable_rls_on_schema`` skips
the template schema by design — provisioning re-applies RLS when a
tenant is cloned from it).

Revision ID: b6e1d8c4a7f2
Revises: 1fc013b22ef5
Create Date: 2026-06-10
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from alembic import op
from sqlalchemy import text
from sqlalchemy.orm import Session

if TYPE_CHECKING:
    from collections.abc import Sequence

__all__ = ["branch_labels", "depends_on", "down_revision", "revision"]

revision: str = "b6e1d8c4a7f2"
down_revision: str | Sequence[str] | None = "1fc013b22ef5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Imported here, not at module level: env.py puts backend/ on sys.path
    # for migration runs, but revision *walkers* (provisioning's
    # stamp-at-head) import every migration module without that setup —
    # a module-level `from app...` import breaks tenant provisioning.
    from app.db import enable_rls_on_schema  # noqa: PLC0415

    bind = op.get_bind()
    schema = bind.execute(text("SELECT current_schema()")).scalar()
    if not schema:
        return
    session = Session(bind=bind)
    enable_rls_on_schema(session, schema)
    # The session wraps alembic's connection/transaction — flush, don't
    # commit/close (alembic owns the transaction; closing would return
    # the connection mid-migration).
    session.flush()


def downgrade() -> None:
    # Healing to the current provisioning shape has no meaningful inverse;
    # the pre-heal state was drift, not a version to restore.
    pass
