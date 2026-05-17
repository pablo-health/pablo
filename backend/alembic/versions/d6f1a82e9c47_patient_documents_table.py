# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""patient_documents table (THERAPY-ak6m.2)

First-class per-tenant table for clinician-uploaded patient documents
(PDFs, PNG/JPEG scans). Lives in each ``practice_*`` schema (not the
shared ``platform`` schema) — these rows are PHI-adjacent and inherit
the tenant-isolation envelope.

Shape:

  * ``id``              UUID, primary key (uuid4 client-side)
  * ``patient_id``      UUID, FK ``patients(id)`` ON DELETE CASCADE
  * ``user_id``         VARCHAR(128), the clinician who uploaded
  * ``filename``        TEXT,         original filename for display
  * ``mime_type``       VARCHAR(100), validated whitelist
  * ``gcs_path``        TEXT,         object name within the documents
                                      bucket; per-tenant prefix
                                      ``<tenant_id>/<uuid>``
  * ``extracted_text``  TEXT NULL,    NULL when PyMuPDF returned
                                      <100 chars (treated as scanned;
                                      ak6m.2.3 will add OCR)
  * ``size_bytes``      BIGINT,       byte count from GCS metadata
  * ``created_at``      TIMESTAMPTZ,  insertion time
  * ``finalized_at``    TIMESTAMPTZ NULL — NULL = upload placeholder
                                          (signed URL minted, file not
                                          yet confirmed in GCS); SET =
                                          finalize step verified the
                                          object and ran extraction
  * ``deleted_at``      TIMESTAMPTZ NULL — soft-delete tombstone

Index ``ix_patient_documents_patient_deleted (patient_id, deleted_at)``
covers the list-by-patient hot path with the live-row filter inline.

RLS policy is created by ``app.db.enable_rls_on_schema`` at tenant
provisioning time. Unlike the patient_access shape used by ``notes``
and other patient-scoped tables, ``patient_documents`` is RLS-keyed
to ``user_id`` — v1 single-clinician document ownership. Cross-
clinician sharing waits for a follow-up bead.

Revision ID: d6f1a82e9c47
Revises: a8f3c7d2b916
Create Date: 2026-05-17
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import sqlalchemy as sa
from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "d6f1a82e9c47"
down_revision: str | Sequence[str] | None = "a8f3c7d2b916"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "patient_documents",
        sa.Column("id", sa.Uuid(as_uuid=False), nullable=False),
        sa.Column("patient_id", sa.Uuid(as_uuid=False), nullable=False),
        sa.Column("user_id", sa.String(length=128), nullable=False),
        sa.Column("filename", sa.Text(), nullable=False),
        sa.Column("mime_type", sa.String(length=100), nullable=False),
        sa.Column("gcs_path", sa.Text(), nullable=False),
        sa.Column("extracted_text", sa.Text(), nullable=True),
        sa.Column(
            "size_bytes",
            sa.BigInteger(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("finalized_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(
            ["patient_id"],
            ["patients.id"],
            ondelete="CASCADE",
        ),
    )
    op.create_index(
        op.f("ix_patient_documents_user_id"),
        "patient_documents",
        ["user_id"],
        unique=False,
    )
    op.create_index(
        "ix_patient_documents_patient_deleted",
        "patient_documents",
        ["patient_id", "deleted_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_patient_documents_patient_deleted",
        table_name="patient_documents",
    )
    op.drop_index(
        op.f("ix_patient_documents_user_id"),
        table_name="patient_documents",
    )
    op.drop_table("patient_documents")
