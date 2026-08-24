# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""patient email-PHI consent columns

Records whether a patient has consented to receive protected health
information by email, so a deployment that sends patient email can keep
clinical detail out of an email when email is not a consented PHI channel
for that patient.

Adds four columns to ``patients``:

- ``phi_email_consent``     BOOLEAN — nullable tri-state: NULL = no record
  on file, TRUE = consented, FALSE = declined. These four columns ARE the
  consent authority — the current decision only. They record no history of
  their own; recording the grant/withdrawal history (who changed it, when,
  why) is the calling application's responsibility.
- ``phi_email_consent_at``  TIMESTAMPTZ — when the decision was recorded.
- ``phi_email_consent_doc`` TEXT — optional reference to a signed consent
  document backing the attestation.
- ``phi_email_consent_by``  VARCHAR(128) — the user who recorded it.

All NULLABLE: existing patient rows have no record on file (NULL), which is
the safe default — absent a recorded consent, callers keep email generic.

``patients`` is a per-tenant table; the runner sets search_path to each
``practice_*`` schema before invoking this migration, so the unqualified
table ref resolves there. Idempotent (``ADD COLUMN IF NOT EXISTS`` /
``DROP COLUMN IF EXISTS``) so the per-tenant fan-out re-runs safely across
schemas that may or may not already have the columns.

Revision ID: c3f8a2d1e947
Revises: a7e3f1b9c204
Create Date: 2026-06-24
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "c3f8a2d1e947"
down_revision: str | Sequence[str] | None = "a7e3f1b9c204"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # search_path is set to the tenant schema by the runner; the unqualified
    # table ref resolves to that schema.
    op.execute("ALTER TABLE patients ADD COLUMN IF NOT EXISTS phi_email_consent BOOLEAN")
    op.execute(
        "ALTER TABLE patients "
        "ADD COLUMN IF NOT EXISTS phi_email_consent_at TIMESTAMP WITH TIME ZONE"
    )
    op.execute("ALTER TABLE patients ADD COLUMN IF NOT EXISTS phi_email_consent_doc TEXT")
    op.execute("ALTER TABLE patients ADD COLUMN IF NOT EXISTS phi_email_consent_by VARCHAR(128)")


def downgrade() -> None:
    op.execute("ALTER TABLE patients DROP COLUMN IF EXISTS phi_email_consent_by")
    op.execute("ALTER TABLE patients DROP COLUMN IF EXISTS phi_email_consent_doc")
    op.execute("ALTER TABLE patients DROP COLUMN IF EXISTS phi_email_consent_at")
    op.execute("ALTER TABLE patients DROP COLUMN IF EXISTS phi_email_consent")
