# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""client_ai_consent_events: what a client said about AI-assisted notes

One row per answer, appended and never changed: whether the client agreed to,
or declined, having sessions recorded, transcribed and drafted into notes.
The current answer is the latest row; no rows means nobody has asked yet.
See ``ClientAiConsentEventRow`` for the columns and why the history is kept.

Per-patient with a ``patient_id`` and no ``user_id``, so the reconcile pass
that follows every migration gives it the ``has_patient_access`` row policy.

Additive only. Idempotent, like every revision in this chain: it is fanned out
once per practice schema.

Revision ID: a3e7c5f19d42
Revises: c7d2a9e4f1b6
Create Date: 2026-10-05
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "a3e7c5f19d42"
down_revision: str | Sequence[str] | None = "c7d2a9e4f1b6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # search_path is set to the tenant schema by the runner; unqualified table
    # refs resolve to that schema.
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS client_ai_consent_events (
            id                   UUID        PRIMARY KEY,
            patient_id           UUID        NOT NULL
                REFERENCES patients (id) ON DELETE CASCADE,
            decision             VARCHAR(16) NOT NULL,
            effective_on         DATE        NOT NULL,
            source               VARCHAR(16) NOT NULL,
            recorded_by          UUID,
            recorded_at          TIMESTAMPTZ NOT NULL,
            intake_submission_id UUID,
            CONSTRAINT ck_client_ai_consent_events_decision
                CHECK (decision IN ('consented', 'declined')),
            CONSTRAINT ck_client_ai_consent_events_source
                CHECK (source IN ('clinician', 'intake_form')),
            CONSTRAINT ck_client_ai_consent_events_submission
                CHECK ((source = 'intake_form') = (intake_submission_id IS NOT NULL)),
            CONSTRAINT ck_client_ai_consent_events_recorded_by
                CHECK (source <> 'clinician' OR recorded_by IS NOT NULL)
        );
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_client_ai_consent_events_patient_recorded "
        "ON client_ai_consent_events (patient_id, recorded_at);"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS client_ai_consent_events CASCADE;")
