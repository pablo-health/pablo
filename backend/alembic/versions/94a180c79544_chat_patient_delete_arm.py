# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Give existing tenants the patient DELETE arm on the chat tables.

``c7d21b9e4a05`` registered ``chat_conversations`` and ``chat_messages``
patient-readable and -writable, which created SELECT, UPDATE and INSERT
policies. It created no DELETE policy, and under ``FORCE ROW LEVEL
SECURITY`` that is not an error but a silence: a patient's purge matched
zero rows and reported success. The tables are now registered deletable
too (``PATIENT_DELETABLE_TABLES``), and this re-runs
``enable_rls_on_schema`` so schemas provisioned before the registration
pick up the arm. Idempotent and self-healing, the same pattern as the
revision it follows.

No column changes, so the tenant template is untouched: policies are
applied at provisioning time, not carried in the template.

Revision ID: 94a180c79544
Revises: c7d21b9e4a05
Create Date: 2026-09-17
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from alembic import op
from sqlalchemy import text
from sqlalchemy.orm import Session

if TYPE_CHECKING:
    from collections.abc import Sequence

__all__ = ["branch_labels", "depends_on", "down_revision", "revision"]

revision: str = "94a180c79544"
down_revision: str | Sequence[str] | None = "c7d21b9e4a05"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Imported here, not at module level: revision walkers import every
    # migration module without env.py's sys.path setup.
    from app.db import enable_rls_on_schema  # noqa: PLC0415

    bind = op.get_bind()
    schema = bind.execute(text("SELECT current_schema()")).scalar()
    if not schema:
        return

    session = Session(bind=bind)
    enable_rls_on_schema(session, schema)
    # The session wraps alembic's connection; flush, never commit or close.
    session.flush()


def downgrade() -> None:
    # Leaving the arm in place keeps the schema agreeing with what
    # provisioning builds; dropping it here would recreate the silent
    # zero-row purge this revision exists to remove.
    pass
