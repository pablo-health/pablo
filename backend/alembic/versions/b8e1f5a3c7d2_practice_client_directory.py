# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""practice_client_directory(): the practice's clients, for matching only

For each live chart in this schema: id, first and last name, date of birth,
email, and the ids of the clinicians holding a grant. Its only caller is the
patient matcher, which must see the whole practice so a colleague's client is
recognised instead of becoming a second chart. Every other read of
``patients`` keeps its row policy unchanged.

The function is created here (and so captured by the template); its owner,
grants and the two policies naming ``pablo_practice_directory`` are applied
by ``app.db.practice_directory.apply_practice_directory_access`` — here, for
a schema that already exists, and from ``enable_rls_on_schema`` at
provisioning and on every migrate fan-out. The template schema gets the
function only: nothing reads it, and the capture carries no owner or grants.

Revision ID: b8e1f5a3c7d2
Revises: a9c3e7d2b518
Create Date: 2026-09-30
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from alembic import op
from sqlalchemy import text

if TYPE_CHECKING:
    from collections.abc import Sequence

__all__ = ["branch_labels", "depends_on", "down_revision", "revision"]

revision: str = "b8e1f5a3c7d2"
down_revision: str | Sequence[str] | None = "a9c3e7d2b518"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Imported here, not at module level: revision walkers import every
    # migration without env.py's sys.path setup (see e2b7c4f19d38).
    from app.db import DEFAULT_PRACTICE_SCHEMA, _validate_schema_name  # noqa: PLC0415
    from app.db.practice_directory import (  # noqa: PLC0415
        apply_practice_directory_access,
        create_directory_function_sql,
    )

    bind = op.get_bind()
    schema = bind.execute(text("SELECT current_schema()")).scalar()
    if not schema:
        return
    _validate_schema_name(schema)
    bind.exec_driver_sql(create_directory_function_sql(schema))
    if schema != DEFAULT_PRACTICE_SCHEMA:
        apply_practice_directory_access(bind, schema)


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS rls_practice_directory_read ON patients")
    op.execute("DROP POLICY IF EXISTS rls_practice_directory_read ON patient_clinicians")
    op.execute("DROP FUNCTION IF EXISTS practice_client_directory()")
