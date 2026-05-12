# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Drop patients.user_id; patient_clinicians is the source of truth.

Follow-up to ``777b846ab944`` (patient_clinicians table). That
migration backfilled one ``role='primary'`` row per existing patient
from ``patients.user_id``, but left the column in place as a
denormalized cache. Keeping it creates two sources of truth for
"who owns this chart" — which is the bug class the access table was
introduced to prevent. A clinician transferring a patient would have
to update both rows, and any read site that goes to the wrong one
silently leaks across the access boundary.

This migration drops the column. Code that previously filtered on
``patients.user_id`` now joins through ``patient_clinicians`` via the
``has_patient_access`` SQL function. The RLS policy on ``patients``
switches from ``user_id = current_user`` to
``has_patient_access(id, current_user)`` on the next
``enable_rls_on_schema`` pass — see ``app/db/__init__.py``.

Pablo has 0 production users today, so this is the cheapest possible
time to do the cleanup. Deferring it would compound the
two-sources-of-truth tax across every future read site.

Idempotent (``IF EXISTS``) so the per-tenant fan-out can re-run safely
across schemas that may or may not have the column.

Revision ID: 9dea1edf7fe0
Revises: 777b846ab944
Create Date: 2026-05-12
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence


revision: str = "9dea1edf7fe0"
down_revision: str | Sequence[str] | None = "777b846ab944"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # The previous RLS bootstrap created an ``rls_user_isolation``
    # policy against ``patients.user_id``. PG refuses to drop the
    # column while the policy references it, so drop the policy first.
    # The next ``enable_rls_on_schema`` run recreates the
    # access-function policy via the patient-id detection path
    # (see ``app/db/__init__.py``).
    op.execute("DROP POLICY IF EXISTS rls_user_isolation ON patients")
    op.execute("DROP INDEX IF EXISTS ix_patients_user_id")
    op.execute("ALTER TABLE patients DROP COLUMN IF EXISTS user_id")


def downgrade() -> None:
    op.execute("ALTER TABLE patients ADD COLUMN user_id VARCHAR(128)")
    # Repopulate from the access table — the primary row's user_id is
    # the legacy value that lived on ``patients.user_id``.
    op.execute(
        """
        UPDATE patients SET user_id = (
            SELECT user_id FROM patient_clinicians
            WHERE patient_clinicians.patient_id = patients.id
              AND patient_clinicians.role = 'primary'
            LIMIT 1
        )
        """
    )
    op.execute("ALTER TABLE patients ALTER COLUMN user_id SET NOT NULL")
    op.execute("CREATE INDEX ix_patients_user_id ON patients (user_id)")
