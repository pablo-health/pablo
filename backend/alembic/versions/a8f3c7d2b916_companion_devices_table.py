# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""companion_devices table (THERAPY-xo0o)

Creates ``platform.companion_devices``, the registry of native companion
installs enrolled by users via OAuth code-exchange. Each row binds a
device public key (Secure Enclave on Mac / TPM or Software KSP on
Windows) to a user_id and install_id; the DPoP middleware
(THERAPY-6qtr) looks up the JWK by ``(user_id, install_id)`` to verify
per-request proofs.

No PHI: install_id is a client-generated random UUID, hostname_hash is
client-side-hashed. Firebase manages refresh tokens; they are NOT
mirrored here.

Revision ID: a8f3c7d2b916
Revises: c8a31f6e2d54
Create Date: 2026-05-16
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "a8f3c7d2b916"
down_revision: str | Sequence[str] | None = "c8a31f6e2d54"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS platform.companion_devices (
            install_id            VARCHAR(64)  PRIMARY KEY,
            user_id               VARCHAR(128) NOT NULL
                REFERENCES platform.users(id) ON DELETE CASCADE,
            device_public_key_jwk JSONB        NOT NULL,
            jkt                   VARCHAR(64)  NOT NULL,
            key_storage           VARCHAR(16)  NOT NULL,
            platform              VARCHAR(16)  NOT NULL,
            os_version            VARCHAR(64),
            hostname_hash         VARCHAR(64),
            enrolled_at           TIMESTAMPTZ  NOT NULL,
            last_seen             TIMESTAMPTZ  NOT NULL,
            revoked_at            TIMESTAMPTZ,
            CONSTRAINT companion_devices_key_storage_chk
                CHECK (key_storage IN ('hardware', 'software')),
            CONSTRAINT companion_devices_platform_chk
                CHECK (platform IN ('mac', 'windows', 'linux'))
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_companion_devices_user_id "
        "ON platform.companion_devices (user_id)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_companion_devices_jkt ON platform.companion_devices (jkt)"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS platform.companion_devices")
