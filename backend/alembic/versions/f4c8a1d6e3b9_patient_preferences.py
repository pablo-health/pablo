# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Patient preferences: how to work with a client, on the client's record

``patient_preferences`` holds one row per preference, of a registered kind
(``app.preferences.kinds``): times that suit, times that don't work, and a
communication and a practical note. Scheduling kinds keep a structured
``value``; ``text`` is the clinician's words. A retired row is kept.

The reconcile pass that follows every migration gives the new table the
``has_patient_access`` row policy, like ``patient_problems``.

Idempotent, like every revision in this chain: it is fanned out once per
practice schema.

Revision ID: f4c8a1d6e3b9
Revises: d2a7c5e19f36
Create Date: 2026-10-26
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "f4c8a1d6e3b9"
down_revision: str | Sequence[str] | None = "d2a7c5e19f36"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # search_path is set to the tenant schema by the runner; unqualified table
    # refs resolve to that schema.
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS patient_preferences (
            id           UUID         PRIMARY KEY,
            patient_id   UUID         NOT NULL
                REFERENCES patients (id) ON DELETE CASCADE,
            domain       VARCHAR(32)  NOT NULL,
            kind         VARCHAR(64)  NOT NULL,
            value        JSONB,
            text         VARCHAR(280),
            strength     VARCHAR(8),
            source       VARCHAR(16)  NOT NULL,
            source_ref   VARCHAR(128),
            stated_at    TIMESTAMPTZ  NOT NULL,
            confirmed_at TIMESTAMPTZ,
            retired_at   TIMESTAMPTZ,
            retired_by   UUID,
            created_by   UUID,
            created_at   TIMESTAMPTZ  NOT NULL,
            updated_at   TIMESTAMPTZ  NOT NULL,
            CONSTRAINT ck_patient_preferences_strength
                CHECK (strength IS NULL OR strength IN ('hard', 'soft')),
            CONSTRAINT ck_patient_preferences_source
                CHECK (source IN ('clinician', 'assistant', 'learned', 'intake', 'migrated')),
            CONSTRAINT ck_patient_preferences_has_content
                CHECK (value IS NOT NULL OR text IS NOT NULL)
        );
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_patient_preferences_active "
        "ON patient_preferences (patient_id, kind) WHERE retired_at IS NULL;"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS patient_preferences;")
