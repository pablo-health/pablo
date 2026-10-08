# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Chart history: a client's history as chart fields, with every earlier value kept

``patient_chart_history`` holds one row per patient per history field: its
current free text (NULL once removed), who wrote it, when, and the note it
was accepted from. The field keys are a constant in the application, not a
column constraint, so a new field needs no migration.

``patient_chart_history_revisions`` is the trail. Every write appends the
value it replaced, with who wrote that value and when, and who replaced it
and when, so what the chart said on any date can be read back. Nothing
updates or deletes a revision; removing a field's value is itself a write.

No existing data is carried over: history has lived only inside note text,
and reading it out of notes is exactly what this table replaces.

Both tables carry ``patient_id`` and no ``user_id``, so the reconcile pass
that follows every migration gives them the ``has_patient_access`` row
policy, like ``patient_problems``. Idempotent, like every revision in this
chain.

Revision ID: b3e9c6d4f217
Revises: a7d3f2c18e64
Create Date: 2026-10-07
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "b3e9c6d4f217"
down_revision: str | Sequence[str] | None = "a7d3f2c18e64"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # search_path is set to the tenant schema by the runner; unqualified table
    # refs resolve to that schema.
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS patient_chart_history (
            id             UUID        PRIMARY KEY,
            patient_id     UUID        NOT NULL
                REFERENCES patients (id) ON DELETE CASCADE,
            field_key      VARCHAR(64) NOT NULL,
            text           TEXT,
            source_note_id UUID
                REFERENCES notes (id) ON DELETE SET NULL,
            updated_by     UUID,
            updated_at     TIMESTAMPTZ NOT NULL,
            CONSTRAINT uq_patient_chart_history_field UNIQUE (patient_id, field_key)
        );
        """
    )
    # The unique constraint's index serves reads by patient; no second index.
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS patient_chart_history_revisions (
            id             UUID        PRIMARY KEY,
            patient_id     UUID        NOT NULL
                REFERENCES patients (id) ON DELETE CASCADE,
            field_key      VARCHAR(64) NOT NULL,
            text           TEXT,
            source_note_id UUID
                REFERENCES notes (id) ON DELETE SET NULL,
            written_by     UUID,
            written_at     TIMESTAMPTZ NOT NULL,
            replaced_by    UUID        NOT NULL,
            replaced_at    TIMESTAMPTZ NOT NULL
        );
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_patient_chart_history_revisions_patient_field "
        "ON patient_chart_history_revisions (patient_id, field_key);"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS patient_chart_history_revisions;")
    op.execute("DROP TABLE IF EXISTS patient_chart_history;")
