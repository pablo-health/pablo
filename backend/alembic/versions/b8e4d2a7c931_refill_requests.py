# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""refill_requests table

A patient's request, from the portal, to have a medication refilled, and
the prescriber's decision on it. One row per request; the decision columns
are written once.

``patient_id`` makes the table row-scoped: ``enable_rls_on_schema`` gives it
the ``has_patient_access`` policy for clinicians, and its registration in
``PATIENT_READABLE_TABLES`` / ``PATIENT_WRITABLE_TABLES`` gives a patient
their own rows and nobody else's.

Idempotent, like every revision in this chain: it is fanned out once per
practice schema.

Revision ID: b8e4d2a7c931
Revises: f4b8c2d91a37
Create Date: 2026-09-27
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "b8e4d2a7c931"
down_revision: str | Sequence[str] | None = "f4b8c2d91a37"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # search_path is set to the tenant schema by the runner; unqualified table
    # refs resolve to that schema.
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS refill_requests (
            id                  UUID          PRIMARY KEY,
            patient_id          UUID          NOT NULL
                REFERENCES patients (id) ON DELETE CASCADE,
            medication_id       UUID
                REFERENCES patient_medications (id) ON DELETE SET NULL,
            medication_text     VARCHAR(200)  NOT NULL,
            pharmacy_text       VARCHAR(200),
            patient_note        TEXT,
            status              VARCHAR(16)   NOT NULL,
            decided_by_user_id  UUID,
            decided_at          TIMESTAMPTZ,
            prescriber_note     TEXT,
            created_at          TIMESTAMPTZ   NOT NULL,
            updated_at          TIMESTAMPTZ   NOT NULL,
            CONSTRAINT ck_refill_requests_status
                CHECK (status IN ('requested','approved','needs_visit','declined'))
        );
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_refill_requests_patient_id ON refill_requests (patient_id);"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_refill_requests_status_created "
        "ON refill_requests (status, created_at);"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS refill_requests CASCADE;")
