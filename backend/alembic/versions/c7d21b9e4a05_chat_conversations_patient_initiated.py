# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Let a conversation have no clinician owner, and give tenants the patient arms.

Two changes that have to travel together, because each is incoherent
without the other.

**``chat_conversations.owner_user_id`` becomes nullable.** The column
records which clinician started a chat. Every conversation had one,
because until now only a clinician could start one. A patient-initiated
conversation has no clinician in the room at all, and the choices for
representing that are a sentinel id or an absent one. Absent wins for the
same reason the patient-actor audit work chose an explicit column over a
sentinel: a sentinel is a value that every existing query silently treats
as a real clinician, whereas NULL makes the "no owner" case something a
query has to handle on purpose. ``NULL`` therefore *means* patient-
initiated, and that is load-bearing rather than incidental — it is half
of the row test that keeps a patient out of the clinician's chats about
them (see ``_patient_principal_predicate_for``).

**Existing tenants get the new patient policies.** ``chat_conversations``
and ``chat_messages`` are now registered patient-readable and -writable,
but registration only reaches schemas provisioned *after* it, since
``enable_rls_on_schema`` runs at provisioning time. So this re-runs it,
which is idempotent and self-healing, exactly as
``e2b7c4f19d38_appointments_patient_self_read`` and
``b6e1d8c4a7f2_heal_tenant_rls_policies`` do.

Order matters within the upgrade: the patient policy text references
``owner_user_id IS NULL``, so the column has to be nullable before the
policies are built against it.

The failure mode this prevents on an unhealed tenant is the quiet one
again. ``chat_conversations`` is patient-scoped through
``has_patient_access``, which consults ``app.current_user_id`` — a GUC a
patient request never arms — and the ``pablo`` role is NOBYPASSRLS. A
patient on an unhealed tenant would therefore see an empty conversation
list rather than an error, and a write would be refused outright with no
indication that the schema, not the request, was wrong.

``tenant_template.sql`` is regenerated separately: ``enable_rls_on_schema``
skips the ``practice`` template by design and provisioning re-applies RLS
when a tenant is cloned from it, but the template does carry the column
definition, so the nullability change belongs in the regen.

Revision ID: c7d21b9e4a05
Revises: 23ca2ee26c74
Create Date: 2026-09-16
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import sqlalchemy as sa
from alembic import op
from sqlalchemy import text
from sqlalchemy.orm import Session

if TYPE_CHECKING:
    from collections.abc import Sequence

__all__ = ["branch_labels", "depends_on", "down_revision", "revision"]

revision: str = "c7d21b9e4a05"
down_revision: str | Sequence[str] | None = "23ca2ee26c74"
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

    op.alter_column(
        "chat_conversations",
        "owner_user_id",
        existing_type=sa.Uuid(as_uuid=False),
        nullable=True,
        schema=schema,
    )

    session = Session(bind=bind)
    enable_rls_on_schema(session, schema)
    # The session wraps alembic's connection/transaction — flush, don't
    # commit/close (alembic owns the transaction; closing would return
    # the connection mid-migration).
    session.flush()


def downgrade() -> None:
    # Deliberately not reinstating NOT NULL. Any patient-initiated
    # conversation written while this revision was applied has a NULL
    # owner and no clinician to attribute it to, so the constraint could
    # only be restored by inventing an owner for someone else's
    # conversation or by deleting it. Both are worse than a nullable
    # column. The policy half is likewise left in place: dropping it would
    # leave the schema disagreeing with what provisioning builds.
    pass
