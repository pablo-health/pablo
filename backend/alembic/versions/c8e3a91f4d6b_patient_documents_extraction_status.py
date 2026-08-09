# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""patient_documents extraction_status column

Adds ``extraction_status`` so a document row can be visible (``finalized_at``
set) before text extraction has run: the finalize endpoint now does cheap
blob validation inline and offloads GCS download + PyMuPDF + Document AI to
a Cloud Tasks worker instead of running it on the HTTP request thread.

* ``NULL`` — extracted synchronously under the old finalize path. Read as
  ``complete`` (see ``app.models.patient_document.ExtractionStatus``); no
  backfill needed, existing rows already carry their final extraction
  result.
* ``'pending'`` — stamped the moment the finalize worker job is enqueued.
* ``'complete'`` / ``'failed'`` — stamped by the worker once it's done.

A CHECK constraint pins the value set the same way ``extracted_via`` does,
rather than a native PG enum, so future additions stay cheap.

Revision ID: c8e3a91f4d6b
Revises: b2f9d3e1a6c4
Create Date: 2026-08-09
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import sqlalchemy as sa
from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "c8e3a91f4d6b"
down_revision: str | Sequence[str] | None = "b2f9d3e1a6c4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "patient_documents",
        sa.Column("extraction_status", sa.String(length=16), nullable=True),
    )
    op.create_check_constraint(
        "ck_patient_documents_extraction_status",
        "patient_documents",
        "extraction_status IS NULL OR extraction_status IN ('pending', 'complete', 'failed')",
    )


def downgrade() -> None:
    op.drop_constraint("ck_patient_documents_extraction_status", "patient_documents", type_="check")
    op.drop_column("patient_documents", "extraction_status")
