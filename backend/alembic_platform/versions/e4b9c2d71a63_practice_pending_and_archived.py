# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""platform.practices.provisioning_status: 'pending' and 'archived'

Two new values for the status the sign-in gate reads:

- ``'pending'``: the practice is registered (its platform rows exist) and its
  schema has not been built yet. A deployment may register a practice and
  build its schema on first use.
- ``'archived'``: the practice's schema was stored outside the database and
  dropped, and is restored on the next sign-in.

Neither has a schema in the database, so the gate answers both at once and
every practice walker skips any practice that is not ``'ready'``. This revision
only widens ``practices_provisioning_status_chk``; nothing here builds or
restores a schema.

Downgrade refuses while any row holds one of the new values, rather than
rewriting it: there is no older value that means "this schema does not
exist", and guessing one would send the walkers into a missing schema.

Idempotent, like the rest of this chain.

Revision ID: e4b9c2d71a63
Revises: c5d1e8a27f43
Create Date: 2026-11-17
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from alembic import op
from sqlalchemy import text

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "e4b9c2d71a63"
down_revision: str | Sequence[str] | None = "c5d1e8a27f43"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_CONSTRAINT = "practices_provisioning_status_chk"


def _replace_check(values: str) -> None:
    op.execute(f"ALTER TABLE platform.practices DROP CONSTRAINT IF EXISTS {_CONSTRAINT}")
    op.execute(
        f"ALTER TABLE platform.practices ADD CONSTRAINT {_CONSTRAINT} "
        f"CHECK (provisioning_status IN ({values}))"
    )


def upgrade() -> None:
    _replace_check("'pending', 'in_progress', 'ready', 'failed', 'archived'")


def downgrade() -> None:
    held = (
        op.get_bind()
        .execute(
            text(
                "SELECT count(*) FROM platform.practices "
                "WHERE provisioning_status IN ('pending', 'archived')"
            )
        )
        .scalar_one()
    )
    if held:
        raise RuntimeError(
            f"{held} practice(s) are 'pending' or 'archived', which the previous "
            f"revision cannot represent. Build or restore their schemas (or delete "
            f"the rows) before downgrading past e4b9c2d71a63."
        )
    _replace_check("'in_progress', 'ready', 'failed'")
