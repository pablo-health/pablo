# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""patient_message_threads lifecycle, assignment and practice-side read mark

Four columns and a foreign key.

``closed_at`` / ``closed_by`` record the end of a conversation next to the
``status`` the table already carried, so "closed" is never a bare flag
nobody can date or attribute. ``assigned_user_id`` says who should answer —
routing for a group practice, never an access rule, which is why no policy
in this schema mentions it. ``clinician_last_read_at`` is one mark for the
practice rather than one per clinician: the thread list asks "has anything
arrived since somebody here looked", and per-clinician state would be a
second table answering a question nobody asks.

The foreign key is the correction. Every other per-patient table in this
schema cascades from ``patients`` — notes, appointments, documents,
sessions — and this one did not, so deleting a patient left their
correspondence behind as rows nothing could reach. Retention here is the
chart's retention; the messages go when the patient does. Added as a plain
validated constraint because the tables are new in this chain and no
deployment can hold a thread whose patient is missing.

The user ids stay plain UUID columns. Users live in ``platform``, and no
per-tenant table references them.

Idempotent, like every revision in this chain: it is fanned out once per
practice schema.

Revision ID: d5e1b7a93c46
Revises: f3c81a4e72d9
Create Date: 2026-09-20
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "d5e1b7a93c46"
down_revision: str | Sequence[str] | None = "f3c81a4e72d9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # search_path is set to the tenant schema by the runner; unqualified table
    # refs resolve to that schema.
    op.execute(
        """
        ALTER TABLE patient_message_threads
            ADD COLUMN IF NOT EXISTS closed_at              TIMESTAMPTZ,
            ADD COLUMN IF NOT EXISTS closed_by              UUID,
            ADD COLUMN IF NOT EXISTS assigned_user_id       UUID,
            ADD COLUMN IF NOT EXISTS clinician_last_read_at TIMESTAMPTZ;
        """
    )
    # ``ADD CONSTRAINT`` has no IF NOT EXISTS form, so the existence test is
    # explicit. The name is the one PostgreSQL generates for an unnamed
    # foreign key, which is what the ORM declares and therefore what the
    # regenerated tenant template carries — the two producers of this schema
    # have to agree down to the constraint name.
    op.execute(
        """
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM pg_constraint
                WHERE conname = 'patient_message_threads_patient_id_fkey'
                  AND conrelid = 'patient_message_threads'::regclass
            ) THEN
                ALTER TABLE patient_message_threads
                    ADD CONSTRAINT patient_message_threads_patient_id_fkey
                    FOREIGN KEY (patient_id) REFERENCES patients (id)
                    ON DELETE CASCADE;
            END IF;
        END $$;
        """
    )


def downgrade() -> None:
    op.execute(
        "ALTER TABLE patient_message_threads "
        "DROP CONSTRAINT IF EXISTS patient_message_threads_patient_id_fkey;"
    )
    op.execute(
        """
        ALTER TABLE patient_message_threads
            DROP COLUMN IF EXISTS clinician_last_read_at,
            DROP COLUMN IF EXISTS assigned_user_id,
            DROP COLUMN IF EXISTS closed_by,
            DROP COLUMN IF EXISTS closed_at;
        """
    )
