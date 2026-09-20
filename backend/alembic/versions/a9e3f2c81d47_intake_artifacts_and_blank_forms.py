# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""patient_intake_artifacts and intake_blank_forms

The two tables a form needs before it can ask for a file.

``patient_intake_artifacts`` links a question to the document that answers
it. The file itself is an ordinary ``patient_documents`` row in the
``intake_artifact`` category, so nothing about storage is new here — what
is new is knowing that this photograph is the back of the card the third
question asked for. Per-patient, so it is registered patient-readable,
patient-writable and patient-deletable in ``app.db`` and gets the ordinary
row policies; ``patient_id`` is denormalized from the assignment and held
in step by the composite foreign key, the same arrangement
``patient_intake_signatures`` uses.

Two rules live in constraints rather than in a caller. ``document_id`` is
unique, so one file answers one question. The partial unique index on
``(assignment_id, item_id, side)`` says one front and one back — partial,
because a question that asks for records rather than a card may well be
sent three files, and those carry no side at all.

``intake_blank_forms`` is the practice's own empty paperwork, for the
fallback where a practice works from paper: a question can offer one for
download before it asks for the filled-in copy back. It is on nobody's
chart, holds nothing about anybody, and is registered not-row-scoped —
its isolation boundary is the tenant schema, like the forms and consent
documents it sits beside.

Idempotent, like every revision in this chain: it is fanned out once per
practice schema.

Revision ID: a9e3f2c81d47
Revises: c5e1d8b47a92
Create Date: 2026-09-20
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "a9e3f2c81d47"
down_revision: str | Sequence[str] | None = "c5e1d8b47a92"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # search_path is set to the tenant schema by the runner; unqualified table
    # refs resolve to that schema.
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS patient_intake_artifacts (
            id            UUID        PRIMARY KEY,
            assignment_id UUID        NOT NULL,
            patient_id    UUID        NOT NULL,
            item_id       UUID        NOT NULL,
            document_id   UUID        NOT NULL UNIQUE,
            side          VARCHAR(8),
            created_at    TIMESTAMPTZ NOT NULL,
            CONSTRAINT ck_patient_intake_artifacts_side
                CHECK (side IS NULL OR side IN ('front','back')),
            CONSTRAINT fk_patient_intake_artifacts_item
                FOREIGN KEY (item_id) REFERENCES intake_item_definitions (id),
            CONSTRAINT fk_patient_intake_artifacts_document
                FOREIGN KEY (document_id) REFERENCES patient_documents (id)
                ON DELETE CASCADE,
            CONSTRAINT fk_patient_intake_artifacts_assignment
                FOREIGN KEY (assignment_id, patient_id)
                REFERENCES patient_intake_assignments (id, patient_id)
                ON DELETE CASCADE
        );
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_patient_intake_artifacts_patient_id "
        "ON patient_intake_artifacts (patient_id);"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_patient_intake_artifacts_assignment_item "
        "ON patient_intake_artifacts (assignment_id, item_id);"
    )
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_patient_intake_artifacts_side "
        "ON patient_intake_artifacts (assignment_id, item_id, side) "
        "WHERE side IS NOT NULL;"
    )

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS intake_blank_forms (
            id           UUID         PRIMARY KEY,
            title        VARCHAR(200) NOT NULL,
            filename     TEXT         NOT NULL,
            mime_type    VARCHAR(100) NOT NULL,
            gcs_path     TEXT         NOT NULL,
            size_bytes   BIGINT       NOT NULL DEFAULT 0,
            uploaded_by  UUID         NOT NULL,
            created_at   TIMESTAMPTZ  NOT NULL,
            finalized_at TIMESTAMPTZ,
            deleted_at   TIMESTAMPTZ
        );
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_intake_blank_forms_deleted "
        "ON intake_blank_forms (deleted_at);"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS patient_intake_artifacts CASCADE;")
    op.execute("DROP TABLE IF EXISTS intake_blank_forms CASCADE;")
