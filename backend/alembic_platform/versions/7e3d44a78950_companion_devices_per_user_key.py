# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""platform.companion_devices: key enrollments by (install_id, user_id)

The desktop companion keeps one install_id and one device key per OS user, no
matter which Pablo account signs in on it. With ``install_id`` as the whole
primary key, the second account to sign in on an install could not enroll:
the exchange reported the enrollment as failed, and because the companion
still sends ``X-Install-ID`` + ``DPoP`` on every request, the proof check then
found the install enrolled to someone else and signed that account out.

Keying by ``(install_id, user_id)`` gives each account its own row for the
same install (and, in practice, the same public key). Rows for different users
never overlap, so one user's enrollment can never rebind another's key.

**Written idempotently**, like the rest of this chain: the platform template
already carries the composite key on a fresh install, so this is a no-op there
and only re-keys a database whose primary key is still ``install_id`` alone.

**Downgrade is lossy by necessity.** Once two users have enrolled the same
install, the single-column key cannot hold both rows. Downgrade keeps the
earliest enrollment per install_id (the owner the old trust-on-first-use rule
would have kept) and deletes the others; those accounts re-enroll — and are
rejected again, as before this revision — on their next sign-in.

No PHI: install_id is a random client UUID.

Revision ID: 7e3d44a78950
Revises: c6f2d8a4e913
Create Date: 2026-10-11
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "7e3d44a78950"
down_revision: str | Sequence[str] | None = "c6f2d8a4e913"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Number of columns in the table's current primary key; 2 means already re-keyed.
_PK_WIDTH = """
    SELECT count(*) FROM pg_index i
    JOIN pg_attribute a ON a.attrelid = i.indrelid AND a.attnum = ANY(i.indkey)
    WHERE i.indrelid = 'platform.companion_devices'::regclass AND i.indisprimary
"""


def upgrade() -> None:
    op.execute(
        f"""
        DO $$
        BEGIN
            IF ({_PK_WIDTH}) <> 2 THEN
                ALTER TABLE platform.companion_devices
                    DROP CONSTRAINT IF EXISTS companion_devices_pkey;
                ALTER TABLE platform.companion_devices
                    ADD CONSTRAINT companion_devices_pkey PRIMARY KEY (install_id, user_id);
            END IF;
        END $$
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DELETE FROM platform.companion_devices d
        USING platform.companion_devices keep
        WHERE d.install_id = keep.install_id
          AND (keep.enrolled_at, keep.user_id::text) < (d.enrolled_at, d.user_id::text)
        """
    )
    op.execute(
        f"""
        DO $$
        BEGIN
            IF ({_PK_WIDTH}) <> 1 THEN
                ALTER TABLE platform.companion_devices
                    DROP CONSTRAINT IF EXISTS companion_devices_pkey;
                ALTER TABLE platform.companion_devices
                    ADD CONSTRAINT companion_devices_pkey PRIMARY KEY (install_id);
            END IF;
        END $$
        """
    )
