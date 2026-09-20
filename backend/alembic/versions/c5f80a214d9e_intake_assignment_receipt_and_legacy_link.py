# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""patient_intake_assignments: receipt_code and legacy_submission_id

Two columns on the assignment row, both about a form that has already been
handed in.

``receipt_code`` is the short code the patient is given when they submit —
eight characters from an alphabet nobody mishears, unique within the
practice schema. Unique because the whole use for it is finding one row by
it when somebody reads it down a phone line; the index is also what
arbitrates between two requests that generate the same code at the same
instant, which no amount of checking in Python can do.

``legacy_submission_id`` links a row adopted from the fixed intake form
that shipped before a practice could build its own. It is unique for the
reason the adoption command is safe to re-run: adopting the same submission
twice is refused by the database, rather than by the command remembering to
look first. NULL on every assignment a patient filled in through the
portal.

Both are nullable, so the revision adds no default to back-fill and rewrites
no rows. The adoption of the legacy submissions is deliberately NOT done
here — it is an operator command (``app.bin.adopt_intake_submissions``) that
runs after the deploy, because it is data movement whose shape a practice's
operator may want to look at before and after, not DDL.

Idempotent, like every revision in this chain: it is fanned out once per
practice schema.

Revision ID: c5f80a214d9e
Revises: e4c92a1d70b6
Create Date: 2026-09-20
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "c5f80a214d9e"
down_revision: str | Sequence[str] | None = "e4c92a1d70b6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # search_path is set to the tenant schema by the runner; unqualified table
    # refs resolve to that schema.
    op.execute(
        "ALTER TABLE patient_intake_assignments ADD COLUMN IF NOT EXISTS receipt_code VARCHAR(16);"
    )
    op.execute(
        "ALTER TABLE patient_intake_assignments ADD COLUMN IF NOT EXISTS legacy_submission_id UUID;"
    )
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_patient_intake_assignments_receipt "
        "ON patient_intake_assignments (receipt_code);"
    )
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_patient_intake_assignments_legacy_submission "
        "ON patient_intake_assignments (legacy_submission_id);"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS uq_patient_intake_assignments_legacy_submission;")
    op.execute("DROP INDEX IF EXISTS uq_patient_intake_assignments_receipt;")
    op.execute("ALTER TABLE patient_intake_assignments DROP COLUMN IF EXISTS legacy_submission_id;")
    op.execute("ALTER TABLE patient_intake_assignments DROP COLUMN IF EXISTS receipt_code;")
