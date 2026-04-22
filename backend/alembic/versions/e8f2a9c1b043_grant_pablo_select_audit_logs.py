"""grant pablo SELECT on audit_logs

Makes the existing privilege explicit so the weekly pentest probe
(which connects as the ``pablo`` Postgres role, same as the app) can
verify table presence and row counts. Idempotent — PostgreSQL's
``GRANT`` is a no-op if the privilege is already held.

No schema change; no data change.

Revision ID: e8f2a9c1b043
Revises: c7f3e1a4b9d2
Create Date: 2026-04-22
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "e8f2a9c1b043"
down_revision: str | Sequence[str] | None = "c7f3e1a4b9d2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("GRANT SELECT ON audit_logs TO pablo;")


def downgrade() -> None:
    op.execute("REVOKE SELECT ON audit_logs FROM pablo;")
