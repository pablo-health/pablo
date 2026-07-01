# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""patient_documents consent category

Adds ``consent`` to the ``patient_documents.category`` allow-list: a
signed consent or authorization form attached to the patient's chart.
It shares CHART's access class — visible to anyone with a
``patient_clinicians`` grant on the patient and releasable via the
standard right-of-access workflow — rather than the uploader-only
restricted categories (``therapist_private``, ``psychotherapy_notes``).

Two objects change together:

* ``ck_patient_documents_category`` — the CHECK constraint gets a
  fourth allowed value.
* ``rls_patient_doc_access`` — the RLS policy's non-restricted branch
  is rewritten from an enumerated ``category = 'chart'`` match to
  ``category NOT IN (<restricted values>)``, so it covers ``consent``
  (and any future non-restricted category) without another migration
  touching this policy. The restricted branch is unchanged.

Revision ID: f4b8c2a91e6d
Revises: c3f8a2d1e947
Create Date: 2026-06-30
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "f4b8c2a91e6d"
down_revision: str | Sequence[str] | None = "c3f8a2d1e947"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint("ck_patient_documents_category", "patient_documents", type_="check")
    op.create_check_constraint(
        "ck_patient_documents_category",
        "patient_documents",
        "category IN ('chart', 'consent', 'therapist_private', 'psychotherapy_notes')",
    )

    op.execute("DROP POLICY IF EXISTS rls_patient_doc_access ON patient_documents")
    op.execute(
        """
        CREATE POLICY rls_patient_doc_access ON patient_documents
        USING (
          (category NOT IN ('therapist_private', 'psychotherapy_notes')
           AND has_patient_access(
             patient_id, current_setting('app.current_user_id', true)
           ))
          OR
          (category IN ('therapist_private', 'psychotherapy_notes')
           AND user_id::text = current_setting('app.current_user_id', true))
        )
        """
    )


def downgrade() -> None:
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
           AND user_id::text = current_setting('app.current_user_id', true))
        )
        """
    )
    op.drop_constraint("ck_patient_documents_category", "patient_documents", type_="check")
    op.create_check_constraint(
        "ck_patient_documents_category",
        "patient_documents",
        "category IN ('chart', 'therapist_private', 'psychotherapy_notes')",
    )
