# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""patient_intake_review_events, and where an answer came from

What the clinician did with a form after the patient handed it in: asked for
corrections on named questions with a note, entered a value for the patient,
or accepted the form. One row per act, in the order they happened, so "why is
this form open again" is answerable from the record rather than inferred from
a status.

``item_ids`` is the questions the act names — the ones a correction reopens,
or the one a clinician-entered value settles — and ``note_to_patient`` is the
sentence the patient reads in the portal. Both are the practice's own words
about the form; neither carries an answer.

Per-patient, so it is registered patient-readable in ``app.db`` and gets the
ordinary row policies. It is deliberately NOT patient-writable: every kind on
it is something the practice did, and the one a patient causes — handing a
corrected form back in — is written by the route that also moves the status.
``patient_id`` is denormalized from the assignment and held in step by the
composite foreign key to ``(id, patient_id)``, the same arrangement
``patient_intake_responses`` uses.

The second half of this revision is one column on ``patient_intake_responses``.
An answer can now reach the chart two ways — the patient typed it in the
portal, or a clinician entered it with the patient in the room — and a reader
who cannot tell the two apart is reading a form that says the patient
attested to something they may never have seen. ``provenance`` is that
distinction, defaulted to ``patient`` because every row written before this
revision was.

Idempotent, like every revision in this chain: it is fanned out once per
practice schema.

Revision ID: c5e1a9f0d248
Revises: f3c81a4e72d9
Create Date: 2026-09-20
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "c5e1a9f0d248"
down_revision: str | Sequence[str] | None = "f3c81a4e72d9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # search_path is set to the tenant schema by the runner; unqualified table
    # refs resolve to that schema.
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS patient_intake_review_events (
            id              UUID        PRIMARY KEY,
            assignment_id   UUID        NOT NULL,
            patient_id      UUID        NOT NULL,
            kind            VARCHAR(24) NOT NULL,
            item_ids        JSONB       NOT NULL DEFAULT '[]'::jsonb,
            note_to_patient TEXT,
            created_by      UUID,
            created_at      TIMESTAMPTZ NOT NULL,
            CONSTRAINT ck_patient_intake_review_events_kind
                CHECK (kind IN ('correction_requested','corrected',
                                'accepted','clinician_entered')),
            CONSTRAINT fk_patient_intake_review_events_assignment
                FOREIGN KEY (assignment_id, patient_id)
                REFERENCES patient_intake_assignments (id, patient_id)
                ON DELETE CASCADE
        );
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_patient_intake_review_events_patient_id "
        "ON patient_intake_review_events (patient_id);"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_patient_intake_review_events_assignment "
        "ON patient_intake_review_events (assignment_id, created_at);"
    )

    op.execute(
        "ALTER TABLE patient_intake_responses "
        "ADD COLUMN IF NOT EXISTS provenance VARCHAR(16) NOT NULL DEFAULT 'patient';"
    )
    # DROP-then-ADD rather than a guarded ADD: Postgres has no
    # ``ADD CONSTRAINT IF NOT EXISTS``, and dropping first is what makes the
    # pair replayable over a schema that already has it.
    op.execute(
        "ALTER TABLE patient_intake_responses "
        "DROP CONSTRAINT IF EXISTS ck_patient_intake_responses_provenance;"
    )
    op.execute(
        "ALTER TABLE patient_intake_responses "
        "ADD CONSTRAINT ck_patient_intake_responses_provenance "
        "CHECK (provenance IN ('patient','clinician'));"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS patient_intake_review_events CASCADE;")
    op.execute(
        "ALTER TABLE patient_intake_responses "
        "DROP CONSTRAINT IF EXISTS ck_patient_intake_responses_provenance;"
    )
    op.execute("ALTER TABLE patient_intake_responses DROP COLUMN IF EXISTS provenance;")
