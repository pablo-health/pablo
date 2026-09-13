# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""payers: remember what the directory said this payer requires

Nullable on purpose, and the distinction between NULL and the empty string is
the whole point. NULL means nobody has asked the directory yet. The empty
string means we asked and the answer was "nothing needs enrolling" — which is
what lets a payer that is ready to bill stop reading as "Not enrolled".

Backfilled for payers that already have requests on file: those were filed
because the directory said so, so what it required is at least the set we
filed. A payer with no requests is left NULL rather than guessed at — absence
of a request is exactly the ambiguity this column exists to resolve, and
writing "" over it would assert the reassuring reading without having asked.

Revision ID: d3e8a1c47b95
Revises: c2f8b40d97ae
Create Date: 2026-09-13
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import sqlalchemy as sa
from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "d3e8a1c47b95"
down_revision: str | Sequence[str] | None = "c2f8b40d97ae"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("payers", sa.Column("directory_requires", sa.String(40), nullable=True))
    op.execute(
        """
        UPDATE payers SET directory_requires = filed.transactions
        FROM (
            SELECT payer_id, string_agg(transaction_type, ',' ORDER BY transaction_type)
                   AS transactions
            FROM payer_enrollments
            GROUP BY payer_id
        ) AS filed
        WHERE payers.id = filed.payer_id
        """
    )


def downgrade() -> None:
    op.drop_column("payers", "directory_requires")
