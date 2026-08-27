# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""audit_logs.actor_type — what kind of principal performed the action

``audit_logs.user_id`` has always been "the actor identifier as recorded": a
plain VARCHAR with no foreign key, deliberately loose so that system actions,
service actions and unauthenticated probes are captured rather than rejected at
INSERT.

That worked while every actor was a clinician. It stops working the moment a
second kind of principal can act, because both identifiers are uuids: a row
saying ``user_id = <uuid>`` can no longer answer "clinician or patient?"
without joining two tables and hoping exactly one matches. This is the six-year
record, read years later by somebody in a dispute, so the ambiguity is a defect
rather than an inconvenience.

``actor_type`` is the discriminator. ``(actor_type, user_id)`` is unambiguous.

SERVER DEFAULT 'clinician', deliberately: every row already in the table was
written by a clinician-or-system actor, and every caller that does not yet set
the field keeps exactly the meaning it had. Nothing existing changes, and two
query shapes that would otherwise break silently keep working — "what did this
clinician do" and "who accessed this patient's record" both stay true.

Chosen over the object-oriented alternative (a parent principal table that
users and patients both key off) on cost: that would mean a new table, a
backfill of every historical actor, and a foreign key on the hottest write path
in the system, to solve what one discriminator column solves.

NOT IN THIS REVISION: the audit_logs RLS policy still compares against
``app.current_user_id``, so a patient-principal INSERT is still refused under
the NOBYPASSRLS role. That policy change is its own piece of work; this column
is the prerequisite, and it is additive and safe on its own.

Revision ID: a91c5d3e7b28
Revises: ea473accf672
Create Date: 2026-08-26
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "a91c5d3e7b28"
down_revision: str | Sequence[str] | None = "ea473accf672"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE audit_logs ADD COLUMN IF NOT EXISTS "
        "actor_type VARCHAR(20) NOT NULL DEFAULT 'clinician'"
    )
    # Every "what did this actor do" query will want to exclude or select one
    # kind, and the table is append-only and large.
    op.execute("CREATE INDEX IF NOT EXISTS ix_audit_logs_actor_type ON audit_logs (actor_type)")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_audit_logs_actor_type")
    op.execute("ALTER TABLE audit_logs DROP COLUMN IF EXISTS actor_type")
