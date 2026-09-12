# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""appointment_types carries the service code it bills as

One nullable column. ``cpt`` is the code a session of this type bills as, and
it belongs here because the code is a property of the SERVICE, not of the
client or the visit: a 45-minute individual session is the same code whether
the clinician is treating anxiety or depression.

It lived only on ``claim_lines`` and ``contracted_rates`` before this, both of
which sit downstream of a filed claim. A self-pay client never generates one,
so the expected code was unreachable for a Good Faith Estimate or a superbill
— the two documents that have to state it and never touch the claims path.

Nullable, and stays nullable: a practice that never bills insurance and never
issues an estimate does not need a code, and a required field here would stand
between a private-pay therapist and getting paid. Free text rather than an enum
because the code set changes without asking us, matching ``claim_lines.cpt``
and ``contracted_rates.cpt``, which are also ``VARCHAR(10)``.

Revision ID: a7d4e91c3b62
Revises: f2a91c4d7e63
Create Date: 2026-09-12
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "a7d4e91c3b62"
down_revision: str | Sequence[str] | None = "f2a91c4d7e63"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("ALTER TABLE appointment_types ADD COLUMN IF NOT EXISTS cpt VARCHAR(10)")


def downgrade() -> None:
    op.execute("ALTER TABLE appointment_types DROP COLUMN IF EXISTS cpt")
