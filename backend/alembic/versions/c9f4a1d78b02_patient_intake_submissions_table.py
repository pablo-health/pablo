# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""patient_intake_submissions table

Adds the per-tenant ``patient_intake_submissions`` table — the raw,
as-submitted patient intake form body. Storage only: the routes that write
it and the scoring that derives an ``outcome_measures`` row from it are
separate changes.

``patient_id`` is UUID so the per-tenant ``has_patient_access`` policy
applies to these rows directly, matching the other per-patient chart
tables. No ``practice_id`` column — tenant scope is implicit in the schema
location. A submission is immutable once recorded, so there is no
``updated_at`` and no soft-delete column.

Deliberately idempotent. A deployment may already carry this table from a
prior extension that created it with exactly this DDL, so the create is a
no-op there and the rows it already holds are simply now engine-owned;
everywhere else this revision creates it. That is also what the tenant
chain needs in general, since it is fanned out once per practice schema.

Revision ID: c9f4a1d78b02
Revises: b3d8f1a06c57
Create Date: 2026-09-19
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "c9f4a1d78b02"
down_revision: str | Sequence[str] | None = "b3d8f1a06c57"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # search_path is set to the tenant schema by the runner; unqualified table
    # refs resolve to that schema.
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS patient_intake_submissions (
            id            VARCHAR(128) PRIMARY KEY,
            patient_id    UUID         NOT NULL,
            submitted_at  TIMESTAMPTZ  NOT NULL,
            payload       JSONB        NOT NULL,
            created_by    VARCHAR(128) NOT NULL,
            created_at    TIMESTAMPTZ  NOT NULL
        );
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_patient_intake_submissions_patient_id "
        "ON patient_intake_submissions (patient_id);"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS patient_intake_submissions CASCADE;")
