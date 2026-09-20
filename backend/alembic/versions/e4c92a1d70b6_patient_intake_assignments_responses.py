# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""patient_intake_assignments and patient_intake_responses tables

The two per-tenant tables behind sending an intake form to somebody and
them filling it in: the request, and the answers saved against it. Storage
and DDL only — the routes that read and write them ship in the same change
but touch no other schema.

Per-patient, unlike the three form-builder tables this revision follows. A
form is the practice's own paperwork; a request to fill one in, and the
answers to it, belong to one person. Both carry ``patient_id`` as UUID so
the per-tenant ``has_patient_access`` policy applies to these rows
directly, matching the other per-patient chart tables. It is denormalized
onto ``patient_intake_responses`` deliberately: every per-patient policy
keys on a ``patient_id`` column, so carrying it means the response table
needs no bespoke policy branch. The composite foreign key to
``(id, patient_id)`` is what keeps that copy honest — hence the unique
constraint on the parent, which is not redundant with its primary key but
is the target the composite key names.

**Two partial unique indexes, and they are the rules of the feature.**

``uq_patient_intake_assignments_active`` says one live request per patient
per version: a second click, or portal access reissued after a link
expired, must not leave somebody holding two copies of one form.
``accepted`` and ``withdrawn`` sit outside the predicate, so a form
genuinely asked for again later is still possible.

``uq_patient_intake_responses_live_draft`` says one live draft per
question per request, which is what makes saving an answer an upsert
rather than an append.

Both are partial, so they are created as indexes rather than as table
constraints — a UNIQUE constraint cannot carry a WHERE clause.

Idempotent, like every revision in this chain: it is fanned out once per
practice schema.

Revision ID: e4c92a1d70b6
Revises: b7e3f0c48d15
Create Date: 2026-09-20
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "e4c92a1d70b6"
down_revision: str | Sequence[str] | None = "b7e3f0c48d15"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # search_path is set to the tenant schema by the runner; unqualified table
    # refs resolve to that schema.
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS patient_intake_assignments (
            id            UUID        PRIMARY KEY,
            patient_id    UUID        NOT NULL,
            version_id    UUID        NOT NULL,
            status        VARCHAR(24) NOT NULL,
            assigned_by   UUID,
            assigned_at   TIMESTAMPTZ NOT NULL,
            submitted_at  TIMESTAMPTZ,
            accepted_at   TIMESTAMPTZ,
            withdrawn_at  TIMESTAMPTZ,
            updated_at    TIMESTAMPTZ NOT NULL,
            CONSTRAINT ck_patient_intake_assignments_status
                CHECK (status IN ('assigned','in_progress','submitted',
                                  'needs_correction','accepted','withdrawn')),
            CONSTRAINT fk_patient_intake_assignments_version
                FOREIGN KEY (version_id)
                REFERENCES intake_packet_versions (id),
            CONSTRAINT uq_patient_intake_assignments_id_patient UNIQUE (id, patient_id)
        );
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_patient_intake_assignments_patient_id "
        "ON patient_intake_assignments (patient_id);"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_patient_intake_assignments_version_id "
        "ON patient_intake_assignments (version_id);"
    )
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_patient_intake_assignments_active "
        "ON patient_intake_assignments (patient_id, version_id) "
        "WHERE status NOT IN ('accepted','withdrawn');"
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS patient_intake_responses (
            id             UUID        PRIMARY KEY,
            assignment_id  UUID        NOT NULL,
            patient_id     UUID        NOT NULL,
            item_id        UUID        NOT NULL,
            value          JSONB       NOT NULL,
            draft          BOOLEAN     NOT NULL DEFAULT TRUE,
            superseded_by  UUID,
            created_at     TIMESTAMPTZ NOT NULL,
            updated_at     TIMESTAMPTZ NOT NULL,
            CONSTRAINT fk_patient_intake_responses_assignment
                FOREIGN KEY (assignment_id, patient_id)
                REFERENCES patient_intake_assignments (id, patient_id)
                ON DELETE CASCADE,
            CONSTRAINT fk_patient_intake_responses_item
                FOREIGN KEY (item_id)
                REFERENCES intake_item_definitions (id)
        );
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_patient_intake_responses_patient_id "
        "ON patient_intake_responses (patient_id);"
    )
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_patient_intake_responses_live_draft "
        "ON patient_intake_responses (assignment_id, item_id) "
        "WHERE superseded_by IS NULL AND draft;"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS patient_intake_responses CASCADE;")
    op.execute("DROP TABLE IF EXISTS patient_intake_assignments CASCADE;")
