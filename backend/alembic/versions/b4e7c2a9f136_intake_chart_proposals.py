# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Chart updates proposed from an accepted intake form

``note_chart_proposals`` gains a second source. A proposal is now either a
note's (``note_id``) or an accepted intake form's (``intake_assignment_id``),
exactly one of the two, checked by ``ck_note_chart_proposals_one_source``.
The form's foreign key is composite with ``patient_id``, the same pattern
``patient_intake_responses`` uses, so the row's ``patient_id`` (which its
``has_patient_access`` policy reads) cannot disagree with the form's. One
proposal per form per field and entry, by a partial unique index. Every
existing row is a note's, so nothing is backfilled.

``patients`` gains ``pronouns`` and ``emergency_contact``, both text as
written, which an intake form's answers can propose. NULL on every existing
row.

Idempotent, like every revision in this chain.

Revision ID: b4e7c2a9f136
Revises: a7d3e9f15c42
Create Date: 2026-10-17
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "b4e7c2a9f136"
down_revision: str | Sequence[str] | None = "a7d3e9f15c42"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # search_path is set to the tenant schema by the runner; unqualified table
    # refs resolve to that schema.
    op.execute("ALTER TABLE patients ADD COLUMN IF NOT EXISTS pronouns VARCHAR(64);")
    op.execute("ALTER TABLE patients ADD COLUMN IF NOT EXISTS emergency_contact TEXT;")
    op.execute("ALTER TABLE note_chart_proposals ALTER COLUMN note_id DROP NOT NULL;")
    op.execute(
        "ALTER TABLE note_chart_proposals ADD COLUMN IF NOT EXISTS intake_assignment_id UUID;"
    )
    op.execute(
        """
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM pg_constraint
                WHERE conname = 'fk_note_chart_proposals_intake'
                  AND conrelid = 'note_chart_proposals'::regclass
            ) THEN
                ALTER TABLE note_chart_proposals
                    ADD CONSTRAINT fk_note_chart_proposals_intake
                    FOREIGN KEY (intake_assignment_id, patient_id)
                    REFERENCES patient_intake_assignments (id, patient_id)
                    ON DELETE CASCADE;
            END IF;
            IF NOT EXISTS (
                SELECT 1 FROM pg_constraint
                WHERE conname = 'ck_note_chart_proposals_one_source'
                  AND conrelid = 'note_chart_proposals'::regclass
            ) THEN
                ALTER TABLE note_chart_proposals
                    ADD CONSTRAINT ck_note_chart_proposals_one_source
                    CHECK (num_nonnulls(note_id, intake_assignment_id) = 1);
            END IF;
        END
        $$;
        """
    )
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_note_chart_proposals_intake_field "
        "ON note_chart_proposals (intake_assignment_id, field_key, item_key) "
        "WHERE intake_assignment_id IS NOT NULL;"
    )


def downgrade() -> None:
    op.execute("DELETE FROM note_chart_proposals WHERE note_id IS NULL;")
    op.execute("DROP INDEX IF EXISTS uq_note_chart_proposals_intake_field;")
    op.execute(
        "ALTER TABLE note_chart_proposals "
        "DROP CONSTRAINT IF EXISTS ck_note_chart_proposals_one_source;"
    )
    op.execute(
        "ALTER TABLE note_chart_proposals DROP CONSTRAINT IF EXISTS fk_note_chart_proposals_intake;"
    )
    op.execute("ALTER TABLE note_chart_proposals DROP COLUMN IF EXISTS intake_assignment_id;")
    op.execute("ALTER TABLE note_chart_proposals ALTER COLUMN note_id SET NOT NULL;")
    op.execute("ALTER TABLE patients DROP COLUMN IF EXISTS emergency_contact;")
    op.execute("ALTER TABLE patients DROP COLUMN IF EXISTS pronouns;")
