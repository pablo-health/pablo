# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Whether the clinician who held a visit is in network with a payer

Adds ``practice_enrollment_status(appointment_id, payer_id)``, the fourth
function the practice directory role owns (``app.db.practice_directory``).
It reads ``platform.payer_participations`` through the grant and policy the
platform chain's ``a6248b0a751e`` gives the role, which runs before this.

Additive only, and idempotent. Downgrade drops the function as its owner;
the columns it reads here are the claim source's too, so no grant goes.

Revision ID: 935993fc0ba8
Revises: d3a7c5e91f24
Create Date: 2026-11-26
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from alembic import op
from sqlalchemy import text

if TYPE_CHECKING:
    from collections.abc import Sequence

    from sqlalchemy.engine import Connection

__all__ = ["branch_labels", "depends_on", "down_revision", "revision"]

revision: str = "935993fc0ba8"
down_revision: str | Sequence[str] | None = "d3a7c5e91f24"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _schema(bind: Connection) -> str:
    """The schema this pass is migrating. A tenant revision always runs in one."""
    from app.db import _validate_schema_name  # noqa: PLC0415

    schema = bind.execute(text("SELECT current_schema()")).scalar()
    if not schema:
        msg = "practice_enrollment_status: no current schema to migrate"
        raise RuntimeError(msg)
    _validate_schema_name(schema)
    return str(schema)


def upgrade() -> None:
    # Imported here, not at module level: revision walkers import every
    # migration without env.py's sys.path setup (see e2b7c4f19d38).
    from app.db import DEFAULT_PRACTICE_SCHEMA  # noqa: PLC0415
    from app.db.practice_directory import (  # noqa: PLC0415
        apply_practice_directory_access,
        create_enrollment_function,
    )

    bind = op.get_bind()
    schema = _schema(bind)
    create_enrollment_function(bind, schema)
    if schema != DEFAULT_PRACTICE_SCHEMA:
        # The template schema carries no policies and nothing reads it.
        apply_practice_directory_access(bind, schema)


def downgrade() -> None:
    from app.db.practice_directory import drop_enrollment_function  # noqa: PLC0415

    drop_enrollment_function(op.get_bind(), _schema(op.get_bind()))
