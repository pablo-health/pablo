# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""patient_documents: a second uploader, and the two categories they use

Until now every row on this table was put there by a clinician, and
``user_id`` said which one. A patient can now add to their own chart, so
the uploader becomes one column or the other:

* ``user_id`` drops NOT NULL,
* ``uploaded_by_patient_id`` arrives,
* ``ck_patient_documents_one_uploader`` makes them exclusive — exactly one
  is set, so a row can neither claim both uploaders nor go unattributed.

The category CHECK gains ``intake_artifact`` (something asked for before a
first appointment — an insurance card, a referral letter) and ``message``
(a file attached to secure correspondence). Both sit in the same access
class as ``chart``; what is particular about them is that they are the two
the patient's own routes read and write.

Existing rows need no backfill: every one of them has a ``user_id``, which
is what the new constraint asks of a clinician upload.

Idempotent, like every revision in this chain: it is fanned out once per
practice schema.

Revision ID: c5e1d8b47a92
Revises: f3c81a4e72d9
Create Date: 2026-09-20
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "c5e1d8b47a92"
down_revision: str | Sequence[str] | None = "f3c81a4e72d9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_CATEGORIES = (
    "'chart', 'consent', 'intake_artifact', 'message', 'therapist_private', 'psychotherapy_notes'"
)


def upgrade() -> None:
    # search_path is set to the tenant schema by the runner; unqualified table
    # refs resolve to that schema.
    op.execute("ALTER TABLE patient_documents ALTER COLUMN user_id DROP NOT NULL;")
    op.execute(
        "ALTER TABLE patient_documents ADD COLUMN IF NOT EXISTS uploaded_by_patient_id UUID;"
    )
    # Drop-then-add rather than a rename: a CHECK cannot be altered in place,
    # and both names are stable, so a re-run lands on the same definition.
    op.execute(
        "ALTER TABLE patient_documents DROP CONSTRAINT IF EXISTS ck_patient_documents_category;"
    )
    op.execute(
        "ALTER TABLE patient_documents ADD CONSTRAINT ck_patient_documents_category "
        f"CHECK (category IN ({_CATEGORIES}));"
    )
    op.execute(
        "ALTER TABLE patient_documents DROP CONSTRAINT IF EXISTS ck_patient_documents_one_uploader;"
    )
    op.execute(
        "ALTER TABLE patient_documents ADD CONSTRAINT ck_patient_documents_one_uploader "
        "CHECK ((user_id IS NOT NULL) <> (uploaded_by_patient_id IS NOT NULL));"
    )


def downgrade() -> None:
    op.execute(
        "ALTER TABLE patient_documents DROP CONSTRAINT IF EXISTS ck_patient_documents_one_uploader;"
    )
    # Rows the pre-revision table cannot represent go with the revision:
    # a patient upload has nowhere to record its uploader once the column is
    # gone (and NOT NULL cannot come back while ``user_id`` is NULL), and the
    # two new categories fail the CHECK restored below. Destructive, and
    # deliberately so — the alternative is a downgrade that leaves the schema
    # refusing to accept its own table.
    op.execute(
        "DELETE FROM patient_documents "
        "WHERE uploaded_by_patient_id IS NOT NULL "
        "OR category IN ('intake_artifact', 'message');"
    )
    op.execute("ALTER TABLE patient_documents DROP COLUMN IF EXISTS uploaded_by_patient_id;")
    op.execute(
        "ALTER TABLE patient_documents DROP CONSTRAINT IF EXISTS ck_patient_documents_category;"
    )
    op.execute(
        "ALTER TABLE patient_documents ADD CONSTRAINT ck_patient_documents_category "
        "CHECK (category IN ('chart', 'consent', 'therapist_private', 'psychotherapy_notes'));"
    )
    op.execute("ALTER TABLE patient_documents ALTER COLUMN user_id SET NOT NULL;")
