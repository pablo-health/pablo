# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""patient_documents.category enum + category-aware RLS (THERAPY-ak6m.2)

Adds the access + disclosure classification column to patient_documents.
Three values, chosen to give us regulatory hooks now and physical-
separation room later (the enum can become a partition predicate if
compliance review pushes for separate tables / buckets):

* ``chart`` — part of the patient record. Shared with co-treating
  clinicians via patient_clinicians grants. Default for new uploads.
* ``therapist_private`` — provider working material. Uploader-only.
  Outside the standard patient record but without the HIPAA carve-out.
* ``psychotherapy_notes`` — HIPAA §164.501 carve-out. Uploader-only.
  Distinct from therapist_private at the disclosure layer:
  §164.508(a)(2) requires a *separate* authorization for release;
  §164.524(a)(1)(i) exempts these from patient right-of-access.

Access semantics for the two restricted categories are identical at
the RLS layer (uploader-only); they're kept distinct so downstream
workflows can branch on the HIPAA-meaningful boundary.

Stored as VARCHAR(32) + CHECK (not a native Postgres enum) so future
value changes — and a potential table split — stay cheap.

Also replaces the table-create RLS policy with the category-aware one:

* OLD (d6f1a82e9c47): visibility = patient_clinicians grant for all rows.
* NEW: ``chart`` keeps the patient_clinicians predicate; restricted
  categories collapse to ``user_id = current_user``.

The application layer (PatientDocumentRepository) enforces the same
predicate; DB-level RLS is the defense-in-depth backstop.

Revision ID: e7a2c91d5f8b
Revises: d6f1a82e9c47
Create Date: 2026-05-17
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import sqlalchemy as sa
from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "e7a2c91d5f8b"
down_revision: str | Sequence[str] | None = "d6f1a82e9c47"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "patient_documents",
        sa.Column(
            "category",
            sa.String(length=32),
            nullable=False,
            server_default=sa.text("'chart'"),
        ),
    )
    op.create_check_constraint(
        "ck_patient_documents_category",
        "patient_documents",
        "category IN ('chart', 'therapist_private', 'psychotherapy_notes')",
    )

    # Replace the table-create policy with the category-aware one.
    op.execute("DROP POLICY IF EXISTS rls_patient_doc_access ON patient_documents")
    op.execute(
        """
        CREATE POLICY rls_patient_doc_access ON patient_documents
        USING (
          (category = 'chart' AND has_patient_access(
            patient_id, current_setting('app.current_user_id', true)
          ))
          OR
          (category IN ('therapist_private', 'psychotherapy_notes')
           AND user_id = current_setting('app.current_user_id', true))
        )
        """
    )


def downgrade() -> None:
    # Restore the patient_clinicians-only policy from the table-create
    # migration; then drop the column. Anyone running downgrade has to
    # accept that previously-restricted rows become co-treater-visible
    # — there is no boolean equivalent that survives the downgrade
    # cleanly.
    op.execute("DROP POLICY IF EXISTS rls_patient_doc_access ON patient_documents")
    op.execute(
        """
        CREATE POLICY rls_patient_doc_access ON patient_documents
        USING (
          has_patient_access(
            patient_id, current_setting('app.current_user_id', true)
          )
        )
        """
    )
    op.drop_constraint("ck_patient_documents_category", "patient_documents", type_="check")
    op.drop_column("patient_documents", "category")
