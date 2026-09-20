# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""instrument_license_attestations

A practice's record that it holds the permission a use-restricted instrument
requires. Some instruments may be reproduced but not used freely — the
wording is published, and the licence covers clinical work and not something
else — and the registry marks those ``attestation_required``. A row here is
the practice saying it holds what that instrument needs; until there is one,
the form builder does not offer the instrument and publishing a form that
asks it is refused.

Practice-level, so it carries no ``patient_id`` and no ``user_id``:
permission is held by the practice, and ``attested_by`` names who recorded
it rather than who owns it. It is registered not-row-scoped in ``app.db``
for that reason — its boundary is the schema.

A row is an act rather than a state. Withdrawing sets ``revoked_at`` and
recording permission again writes a new row, so who said what and when
survives. ``uq_instrument_license_attestations_active`` is partial on
``revoked_at IS NULL`` and is what makes "is this instrument licensed here"
a question with one answer.

Idempotent, like every revision in this chain: it is fanned out once per
practice schema.

Revision ID: e2c94f71b6a3
Revises: d2a70f6c913b
Create Date: 2026-09-20
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "e2c94f71b6a3"
down_revision: str | Sequence[str] | None = "d2a70f6c913b"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # search_path is set to the tenant schema by the runner; unqualified table
    # refs resolve to that schema.
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS instrument_license_attestations (
            id                UUID         PRIMARY KEY,
            instrument_code   VARCHAR(32)  NOT NULL,
            attested_by       UUID         NOT NULL,
            attested_at       TIMESTAMPTZ  NOT NULL,
            license_reference VARCHAR(200),
            notes             TEXT,
            revoked_at        TIMESTAMPTZ
        );
        """
    )
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_instrument_license_attestations_active "
        "ON instrument_license_attestations (instrument_code) "
        "WHERE revoked_at IS NULL;"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS instrument_license_attestations CASCADE;")
