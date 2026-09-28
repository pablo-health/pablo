# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Text the portal sign-in code when it is asked for, not at invite time

``companion_auth_challenges.otp_hash`` becomes nullable: an invitation is now
issued with no code, and the code is texted when the patient opens the link
and asks for one. ``code_expires_at`` is the new code's own window, set on
each request, so a link that lives for days still hands out codes that live
for minutes.

Rows already in flight keep working under the rule they were issued under:
they have a hash and no ``code_expires_at``, and the service treats the
invitation's ``expires_at`` as their window.

Idempotent, like every revision in this chain: it is fanned out once per
practice schema.

Revision ID: d3a7f1c9e254
Revises: b8e4d2a7c931
Create Date: 2026-09-28
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "d3a7f1c9e254"
down_revision: str | Sequence[str] | None = "b8e4d2a7c931"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # search_path is set to the tenant schema by the runner; unqualified table
    # refs resolve to that schema.
    op.execute("ALTER TABLE companion_auth_challenges ALTER COLUMN otp_hash DROP NOT NULL")
    op.execute(
        "ALTER TABLE companion_auth_challenges ADD COLUMN IF NOT EXISTS code_expires_at TIMESTAMPTZ"
    )


def downgrade() -> None:
    # An invitation nobody has asked a code for has no hash to restore. An
    # empty string is not a hex digest, so it can never match a code: those
    # invitations stop redeeming, which is what the old code would have made
    # of them anyway, and the rows stay as the record that access was granted.
    op.execute("UPDATE companion_auth_challenges SET otp_hash = '' WHERE otp_hash IS NULL")
    op.execute("ALTER TABLE companion_auth_challenges ALTER COLUMN otp_hash SET NOT NULL")
    op.execute("ALTER TABLE companion_auth_challenges DROP COLUMN IF EXISTS code_expires_at")
