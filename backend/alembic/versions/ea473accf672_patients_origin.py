# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""patients.origin — provenance marker for rows created through an
unauthenticated intake surface

Most patient rows are created by staff inside the normal chart flow, and
that path can trust the identity it's given: someone at the practice typed
it in. An intake surface that takes a caller's word for who they are cannot
make the same assumption, so a row it creates may in fact duplicate an
existing chart — but it must never guess at that duplication itself (no
lookup by claimed email, phone, or name; a caller can claim to be anyone).
The safe move is to always create a new row and mark it for a human to
reconcile.

``origin`` is that mark. NULL for every existing row and for anything
created by staff — the common case stays unmarked. A non-NULL value names
which intake surface created the row.

Revision ID: ea473accf672
Revises: a1c7f6e93d2b
Create Date: 2026-08-26
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "ea473accf672"
down_revision: str | Sequence[str] | None = "a1c7f6e93d2b"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("ALTER TABLE patients ADD COLUMN IF NOT EXISTS origin VARCHAR(20) NULL")


def downgrade() -> None:
    op.execute("ALTER TABLE patients DROP COLUMN IF EXISTS origin")
