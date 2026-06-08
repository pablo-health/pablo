"""prescribing encounter signature, attestation statement, clinical reasoning

Adds the columns the Clinical Decision Summary freezes into a signed record:

* ``finalized_by`` — the prescriber (user id) who finalized/signed the
  encounter,
* ``attestation_statement`` — their attestation statement, in the clinician's
  own words, and
* ``clinical_reasoning`` — the prescriber's reasoning for the decision (free
  text, clinician-authored, written while the encounter is open).

All are committed to by the ``integrity_digest`` (``clinical_reasoning`` is
part of the snapshot, so a post-signing edit breaks the digest). Per-tenant
(each ``practice_{id}`` schema); access is the application-layer
``has_patient_access`` check via ``patient_id``, same as the rest of the
encounter.

Revision ID: e2b9d4a1c6f3
Revises: d1a47f3c9b62
Create Date: 2026-06-07
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import sqlalchemy as sa
from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "e2b9d4a1c6f3"
down_revision: str | Sequence[str] | None = "d1a47f3c9b62"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "prescribing_encounters",
        sa.Column("finalized_by", sa.String(length=128), nullable=True),
    )
    op.add_column(
        "prescribing_encounters",
        sa.Column("attestation_statement", sa.Text(), nullable=True),
    )
    op.add_column(
        "prescribing_encounters",
        sa.Column("clinical_reasoning", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("prescribing_encounters", "clinical_reasoning")
    op.drop_column("prescribing_encounters", "attestation_statement")
    op.drop_column("prescribing_encounters", "finalized_by")
