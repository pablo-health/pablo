# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""passkey_credentials + passkey_challenges tables

Creates ``platform.passkey_credentials`` (a user's registered WebAuthn
passkeys — one row per authenticator, the possession factor) and
``platform.passkey_challenges`` (the single-use store for in-flight
registration/authentication ceremonies; only the SHA-256 of the challenge
is stored, never the raw value).

The ``user_id`` FK to ``platform.users(id)`` is declared here in raw SQL
rather than on the ORM model: ``PlatformBase.metadata.create_all`` runs at
alembic env bootstrap before migrations, and an ORM-level ForeignKey would
emit the FK while ``users.id`` may be transiently varchar, tripping a
uuid<->varchar mismatch. ``user_id`` is ``UUID`` to match ``platform.users.id``
(converted to native uuid by c1d7e4a9f2b6, which precedes this revision).

No PHI: authenticator metadata, a user-chosen label, and ceremony hashes
only. Shared ``platform`` schema (no RLS), same scope as ``companion_devices``.

Revision ID: 9f4c1a7b2e60
Revises: a7e3f1b9c204
Create Date: 2026-06-15
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "9f4c1a7b2e60"
down_revision: str | Sequence[str] | None = "a7e3f1b9c204"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS platform.passkey_credentials (
            credential_id   VARCHAR(255) PRIMARY KEY,
            user_id         UUID         NOT NULL
                REFERENCES platform.users(id) ON DELETE CASCADE,
            public_key      BYTEA        NOT NULL,
            sign_count      BIGINT       NOT NULL DEFAULT 0,
            transports      JSONB,
            aaguid          VARCHAR(36),
            backup_eligible BOOLEAN      NOT NULL DEFAULT FALSE,
            backup_state    BOOLEAN      NOT NULL DEFAULT FALSE,
            device_label    VARCHAR(120),
            created_at      TIMESTAMPTZ  NOT NULL,
            last_used_at    TIMESTAMPTZ,
            revoked_at      TIMESTAMPTZ,
            CONSTRAINT passkey_credentials_sign_count_chk CHECK (sign_count >= 0)
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_passkey_credentials_user_id "
        "ON platform.passkey_credentials (user_id)"
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS platform.passkey_challenges (
            challenge_hash VARCHAR(64)  PRIMARY KEY,
            ceremony       VARCHAR(16)  NOT NULL,
            -- Nullable: a usernameless (resident-key) authentication ceremony
            -- has no bound user at begin time.
            user_id        UUID         REFERENCES platform.users(id) ON DELETE CASCADE,
            created_at     TIMESTAMPTZ  NOT NULL,
            expires_at     TIMESTAMPTZ  NOT NULL,
            consumed_at    TIMESTAMPTZ,
            CONSTRAINT passkey_challenges_ceremony_chk
                CHECK (ceremony IN ('register', 'authenticate'))
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_passkey_challenges_user_id "
        "ON platform.passkey_challenges (user_id)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_passkey_challenges_expires_at "
        "ON platform.passkey_challenges (expires_at)"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS platform.passkey_challenges")
    op.execute("DROP TABLE IF EXISTS platform.passkey_credentials")
