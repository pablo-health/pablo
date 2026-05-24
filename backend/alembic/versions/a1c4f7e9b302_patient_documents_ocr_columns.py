# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""patient_documents OCR provenance columns (THERAPY-ak6m.2.3)

Adds two diagnostic columns so a doc row records *how* its
``extracted_text`` was produced:

* ``extracted_via`` — NULL until finalize. After finalize, one of
  ``pymupdf`` (native-text PDF), ``document_ai`` (scanned PDF that
  fell back to the Document AI OCR processor), or ``unavailable``
  (OCR attempted but failed or skipped). NULL is also the steady
  state for PNG/JPEG rows — we never extract text from images.
* ``extraction_metadata`` — JSONB blob of OCR diagnostics
  (page_count, avg_confidence, low_confidence_pages, latency_ms).
  NULL for non-OCR rows. Stored as JSONB so future fields
  (processor version, model revision) don't require another
  migration.

Both columns default to NULL on existing rows. No backfill — the
columns are diagnostic; the chat bundler still keys off
``extracted_text IS NULL`` and the legacy "scanned PDF with NULL
text" rows continue to surface as ``skipped_no_text`` exactly as
before.

A CHECK constraint pins the ``extracted_via`` value set rather than
a native PG enum so future additions (e.g. a separate vision
provider) stay cheap.

Revision ID: a1c4f7e9b302
Revises: 987044c1f592
Create Date: 2026-05-24
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "a1c4f7e9b302"
down_revision: str | Sequence[str] | None = "987044c1f592"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "patient_documents",
        sa.Column("extracted_via", sa.String(length=32), nullable=True),
    )
    op.add_column(
        "patient_documents",
        sa.Column("extraction_metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    op.create_check_constraint(
        "ck_patient_documents_extracted_via",
        "patient_documents",
        "extracted_via IS NULL OR extracted_via IN ('pymupdf', 'document_ai', 'unavailable')",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_patient_documents_extracted_via", "patient_documents", type_="check"
    )
    op.drop_column("patient_documents", "extraction_metadata")
    op.drop_column("patient_documents", "extracted_via")
