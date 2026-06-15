# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""clinician_profiles gains credential_titles

The credential set is moving from a single freeform ``credentials``
string to a structured, multi-value list (a clinician may hold several
titles at once, e.g. ``["PMHNP-BC", "RN"]``). ``credential_titles`` is
the JSONB source of truth; the existing ``credentials`` column is kept
as the joined display string derived from it, so signature lines and
other readers of the legacy field are unaffected.

Unqualified table name resolves via search_path to the active practice
schema (the ``practice`` template at deploy time, each tenant during the
per-tenant fan-out). ``ADD COLUMN IF NOT EXISTS`` makes the statement a
no-op on schemas that already have the column, so it is safe to re-run
under the fan-out.

Revision ID: c4b9f2e7a610
Revises: e1d4c7a93f08
Create Date: 2026-06-15
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

__all__ = ["branch_labels", "depends_on", "down_revision", "revision"]

revision: str = "c4b9f2e7a610"
down_revision: str | Sequence[str] | None = "e1d4c7a93f08"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("ALTER TABLE clinician_profiles ADD COLUMN IF NOT EXISTS credential_titles JSONB")


def downgrade() -> None:
    op.execute("ALTER TABLE clinician_profiles DROP COLUMN IF EXISTS credential_titles")
