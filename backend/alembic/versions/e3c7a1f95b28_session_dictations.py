"""session_dictations: what a clinician dictated about a session afterwards

One row per clip dictated after the recording stopped: where the audio is,
what it said once transcribed, and whether it went into the note's redraft
or became a draft addendum to a signed note. Kept apart from the session's
transcript and timing, because dictation is documentation time. See
``SessionDictationRow``.

Carries ``note_id``; the reconcile pass that follows every migration gives it
the note-child row policy, so it is readable exactly when its note is.

Additive only. Idempotent, like every revision in this chain: it is fanned out
once per practice schema.

Revision ID: e3c7a1f95b28
Revises: d5b8e2a71c94
Create Date: 2026-10-06
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "e3c7a1f95b28"
down_revision: str | Sequence[str] | None = "d5b8e2a71c94"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # search_path is set to the tenant schema by the runner; unqualified table
    # refs resolve to that schema.
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS session_dictations (
            id               UUID         PRIMARY KEY,
            session_id       UUID         NOT NULL
                REFERENCES therapy_sessions (id) ON DELETE CASCADE,
            note_id          UUID         NOT NULL
                REFERENCES notes (id) ON DELETE CASCADE,
            patient_id       UUID         NOT NULL
                REFERENCES patients (id) ON DELETE CASCADE,
            author_user_id   UUID         NOT NULL,
            audio_path       TEXT         NOT NULL,
            content_type     VARCHAR(100) NOT NULL,
            duration_seconds INTEGER,
            status           VARCHAR(20)  NOT NULL,
            transcript       TEXT,
            used_as          VARCHAR(20),
            addendum_id      UUID
                REFERENCES note_addenda (id) ON DELETE SET NULL,
            created_at       TIMESTAMPTZ  NOT NULL,
            transcribed_at   TIMESTAMPTZ,
            CONSTRAINT ck_session_dictations_status
                CHECK (status IN ('transcribing', 'transcribed', 'failed')),
            CONSTRAINT ck_session_dictations_used_as
                CHECK (used_as IS NULL OR used_as IN ('redraft', 'addendum'))
        );
        """
    )
    for column in ("session_id", "note_id", "patient_id"):
        op.execute(
            f"CREATE INDEX IF NOT EXISTS ix_session_dictations_{column} "
            f"ON session_dictations ({column});"
        )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS session_dictations CASCADE;")
