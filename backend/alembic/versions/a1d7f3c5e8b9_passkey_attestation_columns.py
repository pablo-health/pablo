# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""passkey attestation provenance columns

Adds ``fmt`` (the WebAuthn attestation statement format, e.g. 'packed',
'apple', 'fido-u2f', 'tpm', 'none') and ``attestation_verified`` (whether
the attestation certificate chain validated to a curated trust root) to
``platform.passkey_credentials``.

The conveyance commit requests attestation and logs the format/aaguid but
persists no provenance verdict. These columns record, per credential,
which authenticator model attested and whether that attestation was
cryptographically trusted — the signal admin hardware-key enforcement and
the manage UI read. ``attestation_verified`` defaults false so existing
rows (enrolled before verification existed) read as unverified.

Platform schema (no RLS), same scope as the table it extends. No PHI.
See PABLO-f00.

Revision ID: a1d7f3c5e8b9
Revises: d3a7f1b8e2c4
Create Date: 2026-06-16
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "a1d7f3c5e8b9"
down_revision: str | Sequence[str] | None = "d3a7f1b8e2c4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE platform.passkey_credentials "
        "ADD COLUMN IF NOT EXISTS fmt VARCHAR(32)"
    )
    op.execute(
        "ALTER TABLE platform.passkey_credentials "
        "ADD COLUMN IF NOT EXISTS attestation_verified BOOLEAN NOT NULL DEFAULT FALSE"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE platform.passkey_credentials DROP COLUMN IF EXISTS attestation_verified")
    op.execute("ALTER TABLE platform.passkey_credentials DROP COLUMN IF EXISTS fmt")
