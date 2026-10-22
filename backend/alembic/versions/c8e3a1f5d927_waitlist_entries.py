# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""waitlist_entries table

``waitlist_entries`` holds existing clients waiting for a better or more
regular time with a clinician: the days and parts of the day they want, in
person or by video, the session type, and where they sit in the clinician's
own order. ``paused`` and ``no_auto_offer`` keep an entry on the list without
it being offered a time automatically; ``filled_at`` marks one whose wait is
over.

It carries ``patient_id``, so ``enable_rls_on_schema`` gives it the
``has_patient_access`` policy for clinicians. Clients never read it.

Idempotent, like every revision in this chain: it is fanned out once per
practice schema.

Revision ID: c8e3a1f5d927
Revises: f4b2c8e1a937
Create Date: 2026-10-22
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "c8e3a1f5d927"
down_revision: str | Sequence[str] | None = "f4b2c8e1a937"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # search_path is set to the tenant schema by the runner; unqualified table
    # refs resolve to that schema.
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS waitlist_entries (
            id             UUID          PRIMARY KEY,
            user_id        UUID          NOT NULL,
            patient_id     UUID          NOT NULL
                REFERENCES patients (id) ON DELETE CASCADE,
            days           JSONB         NOT NULL,
            times          JSONB         NOT NULL,
            modality       VARCHAR(16)   NOT NULL,
            session_type   VARCHAR(30),
            position       INTEGER       NOT NULL,
            paused         BOOLEAN       NOT NULL DEFAULT false,
            no_auto_offer  BOOLEAN       NOT NULL DEFAULT false,
            filled_at      TIMESTAMPTZ,
            created_by     UUID,
            created_at     TIMESTAMPTZ   NOT NULL,
            updated_at     TIMESTAMPTZ   NOT NULL,
            CONSTRAINT ck_waitlist_entries_modality
                CHECK (modality IN ('in_person','telehealth','either'))
        );
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_waitlist_entries_patient_id "
        "ON waitlist_entries (patient_id);"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_waitlist_entries_user_position "
        "ON waitlist_entries (user_id, position);"
    )
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_waitlist_entries_open "
        "ON waitlist_entries (user_id, patient_id) WHERE filled_at IS NULL;"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS waitlist_entries CASCADE;")
