# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""homework_assignments table

``homework_assignments`` holds what a client was asked to do between visits:
assigned by the clinician, either by accepting a homework suggestion from a
signed note (a ``note_chart_proposals`` row, named by ``proposal_id``) or by
hand, and marked done by the client.

It carries ``patient_id``, so ``enable_rls_on_schema`` gives it the
``has_patient_access`` policy for clinicians. It is also registered in
``PATIENT_READABLE_TABLES`` / ``PATIENT_WRITABLE_TABLES`` with a status test,
so a client reads their open and done assignments and nothing else.

Idempotent, like every revision in this chain: it is fanned out once per
practice schema.

Revision ID: d3a7f1c58e20
Revises: b4e7c2a9f136
Create Date: 2026-10-18
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "d3a7f1c58e20"
down_revision: str | Sequence[str] | None = "b4e7c2a9f136"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # search_path is set to the tenant schema by the runner; unqualified table
    # refs resolve to that schema.
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS homework_assignments (
            id             UUID          PRIMARY KEY,
            patient_id     UUID          NOT NULL
                REFERENCES patients (id) ON DELETE CASCADE,
            note_id        UUID
                REFERENCES notes (id) ON DELETE SET NULL,
            proposal_id    UUID
                REFERENCES note_chart_proposals (id) ON DELETE SET NULL,
            source_field   VARCHAR(200),
            proposed_text  TEXT,
            body           TEXT          NOT NULL,
            status         VARCHAR(16)   NOT NULL,
            origin         VARCHAR(16)   NOT NULL,
            assigned_by    UUID,
            assigned_at    TIMESTAMPTZ   NOT NULL,
            withdrawn_by   UUID,
            withdrawn_at   TIMESTAMPTZ,
            completed_at   TIMESTAMPTZ,
            client_note    VARCHAR(500),
            created_at     TIMESTAMPTZ   NOT NULL,
            updated_at     TIMESTAMPTZ   NOT NULL,
            CONSTRAINT ck_homework_assignments_status
                CHECK (status IN ('assigned','done','withdrawn')),
            CONSTRAINT ck_homework_assignments_origin
                CHECK (origin IN ('note','clinician'))
        );
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_homework_assignments_patient_id "
        "ON homework_assignments (patient_id);"
    )
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_homework_assignments_proposal "
        "ON homework_assignments (proposal_id) WHERE proposal_id IS NOT NULL;"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS homework_assignments CASCADE;")
