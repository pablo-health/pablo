# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""passkey_backup_codes table

Creates ``platform.passkey_backup_codes`` — Layer-1 of the account-recovery
model (see docs/security/account-recovery-procedure.md and
authentication-mfa-policy.md §6.4). One row per one-time recovery code,
storing only the SHA-256 hash (never the plaintext, shown once at issuance).
``consumed_at`` non-null marks a code spent (single-use). The ``user_id`` FK
to ``platform.users(id)`` is declared here in raw SQL rather than on the ORM
model — same reason as PasskeyCredentialRow (create_all runs before
migrations at env bootstrap, when users.id may be transiently varchar).

No PHI: a per-user set of hashed recovery secrets. Shared ``platform`` schema
(no RLS), same scope as passkey_credentials. See PABLO-e82.

Revision ID: b2e8c4f6a1d3
Revises: a1d7f3c5e8b9
Create Date: 2026-06-16
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "b2e8c4f6a1d3"
down_revision: str | Sequence[str] | None = "a1d7f3c5e8b9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS platform.passkey_backup_codes (
            code_hash   VARCHAR(64) PRIMARY KEY,
            user_id     UUID        NOT NULL
                REFERENCES platform.users(id) ON DELETE CASCADE,
            created_at  TIMESTAMPTZ NOT NULL,
            consumed_at TIMESTAMPTZ
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_passkey_backup_codes_user_id "
        "ON platform.passkey_backup_codes (user_id)"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS platform.passkey_backup_codes")
