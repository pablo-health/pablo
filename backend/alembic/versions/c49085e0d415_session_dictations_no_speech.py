# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""session_dictations: a clip with no speech in it is its own outcome

``session_dictations.status`` gains ``no_speech``: the clip was transcribed
and held no words (a muted or silent microphone). Until now that was recorded
as ``failed``, which told the clinician transcription had gone wrong when it
hadn't. Existing rows are left as they are. Idempotent, like every revision in
this chain.

Revision ID: c49085e0d415
Revises: a3d8f1c6e902
Create Date: 2026-10-10
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "c49085e0d415"
down_revision: str | Sequence[str] | None = "a3d8f1c6e902"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # search_path is set to the tenant schema by the runner; unqualified table
    # refs resolve to that schema.
    op.execute(
        """
        ALTER TABLE session_dictations
            DROP CONSTRAINT IF EXISTS ck_session_dictations_status;
        ALTER TABLE session_dictations
            ADD CONSTRAINT ck_session_dictations_status
            CHECK (status IN ('transcribing', 'transcribed', 'failed', 'no_speech'));
        """
    )


def downgrade() -> None:
    op.execute(
        """
        UPDATE session_dictations SET status = 'failed' WHERE status = 'no_speech';
        ALTER TABLE session_dictations
            DROP CONSTRAINT IF EXISTS ck_session_dictations_status;
        ALTER TABLE session_dictations
            ADD CONSTRAINT ck_session_dictations_status
            CHECK (status IN ('transcribing', 'transcribed', 'failed'));
        """
    )
