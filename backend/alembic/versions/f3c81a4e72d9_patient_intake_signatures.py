# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""patient_intake_signatures table

What somebody typed their name against, and everything needed to read that
back later: which revision of which document, the digest of its words, who
signed and in what role, when, from which portal session, and how strongly
that session had proved who was holding it.

Per-patient, so it is registered patient-readable and patient-writable in
``app.db`` and gets the ordinary row policies. ``patient_id`` is
denormalized from the assignment and held in step by the composite foreign
key to ``(id, patient_id)`` — the same arrangement
``patient_intake_responses`` uses, and what lets both tables be policied by
a plain column comparison rather than a join.

**One rule lives in an index.** ``uq_patient_intake_signatures_live`` is
partial on ``superseded_at IS NULL`` and says one role signs one item on one
form once. Partial, so it has to be an index rather than a table constraint;
a signature retired by a newer document version is superseded rather than
deleted, because a signature that was taken stays a fact.

Idempotent, like every revision in this chain: it is fanned out once per
practice schema.

Revision ID: f3c81a4e72d9
Revises: b7d4e05c318a
Create Date: 2026-09-20
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "f3c81a4e72d9"
down_revision: str | Sequence[str] | None = "b7d4e05c318a"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # search_path is set to the tenant schema by the runner; unqualified table
    # refs resolve to that schema.
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS patient_intake_signatures (
            id                        UUID         PRIMARY KEY,
            assignment_id             UUID         NOT NULL,
            patient_id                UUID         NOT NULL,
            item_id                   UUID         NOT NULL,
            document_version_id       UUID         NOT NULL,
            document_digest           VARCHAR(64)  NOT NULL,
            signer_role               VARCHAR(16)  NOT NULL,
            signer_typed_name         VARCHAR(160) NOT NULL,
            consent_statement_version VARCHAR(16)  NOT NULL,
            signed_at                 TIMESTAMPTZ  NOT NULL,
            auth_strength             VARCHAR(16)  NOT NULL,
            session_id                VARCHAR(64),
            ip                        VARCHAR(45),
            user_agent                VARCHAR(512),
            evidence_digest           VARCHAR(64)  NOT NULL,
            superseded_at             TIMESTAMPTZ,
            created_at                TIMESTAMPTZ  NOT NULL,
            CONSTRAINT ck_patient_intake_signatures_role
                CHECK (signer_role IN ('patient','guardian')),
            CONSTRAINT fk_patient_intake_signatures_item
                FOREIGN KEY (item_id) REFERENCES intake_item_definitions (id),
            CONSTRAINT fk_patient_intake_signatures_document
                FOREIGN KEY (document_version_id) REFERENCES intake_documents (id),
            CONSTRAINT fk_patient_intake_signatures_assignment
                FOREIGN KEY (assignment_id, patient_id)
                REFERENCES patient_intake_assignments (id, patient_id)
                ON DELETE CASCADE
        );
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_patient_intake_signatures_patient_id "
        "ON patient_intake_signatures (patient_id);"
    )
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_patient_intake_signatures_live "
        "ON patient_intake_signatures (assignment_id, item_id, signer_role) "
        "WHERE superseded_at IS NULL;"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS patient_intake_signatures CASCADE;")
