# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""modality, client_stated_location and consented_by on client_ai_consent_events

Three things about how a client's answer was given, all optional:

* ``modality`` — ``in_person`` or ``telehealth``;
* ``client_stated_location`` — where the client said they were, in their
  words, for a telehealth answer;
* ``consented_by`` — ``client``, ``parent`` or ``guardian``.

Additive only, no backfill: every answer recorded before this reads NULL for
all three. Idempotent: fanned out once per practice schema.

Revision ID: d2a7c4e9f185
Revises: c3f9a1d6e2b8
Create Date: 2026-10-06
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "d2a7c4e9f185"
down_revision: str | Sequence[str] | None = "c3f9a1d6e2b8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "client_ai_consent_events"
_CHECKS = {
    "ck_client_ai_consent_events_modality": (
        "modality IS NULL OR modality IN ('in_person', 'telehealth')"
    ),
    "ck_client_ai_consent_events_consented_by": (
        "consented_by IS NULL OR consented_by IN ('client', 'parent', 'guardian')"
    ),
}


def upgrade() -> None:
    op.execute(f"ALTER TABLE {_TABLE} ADD COLUMN IF NOT EXISTS modality VARCHAR(16) NULL")
    op.execute(f"ALTER TABLE {_TABLE} ADD COLUMN IF NOT EXISTS client_stated_location TEXT NULL")
    op.execute(f"ALTER TABLE {_TABLE} ADD COLUMN IF NOT EXISTS consented_by VARCHAR(16) NULL")
    for name, check in _CHECKS.items():
        op.execute(f"ALTER TABLE {_TABLE} DROP CONSTRAINT IF EXISTS {name}")
        op.execute(f"ALTER TABLE {_TABLE} ADD CONSTRAINT {name} CHECK ({check})")


def downgrade() -> None:
    for name in _CHECKS:
        op.execute(f"ALTER TABLE {_TABLE} DROP CONSTRAINT IF EXISTS {name}")
    for column in ("consented_by", "client_stated_location", "modality"):
        op.execute(f"ALTER TABLE {_TABLE} DROP COLUMN IF EXISTS {column}")
