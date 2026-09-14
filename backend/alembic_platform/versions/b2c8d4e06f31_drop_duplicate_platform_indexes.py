# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Drop 15 duplicate platform indexes

Each of these indexes covers a table and column list that another index already
covers. They exist because the platform schema had two builders that did not
know about each other: ``PlatformBase.metadata.create_all`` created one from the
model, under the ORM naming convention (``ix_platform_<table>_<column>``), and a
migration in the tenant chain created its own with a hand-written name
(``ix_<table>_<column>``). The migration's ``CREATE INDEX IF NOT EXISTS`` matches
on *name*, so it did not notice an identical index already standing there under a
different one.

Every duplicate costs a second btree on every insert, update and delete of the
covered column, for a read pattern one index already serves. ``platform_audit_logs``
carries four such pairs, so every audit write has been maintaining eight indexes
where four would do — on the table that every audited action writes to.

The surviving index in each pair is the model-named one, which is what the ORM
declares and therefore what ``alembic -n platform check`` will keep asserting.
Verified before writing this: none of the fifteen is UNIQUE, each has a
surviving twin on the same table and columns, and no application code names any
of them.

``IF EXISTS`` on every drop, because this revision has two audiences: a database
that predates the platform chain, where all fifteen are present and this is the
repair; and a fresh install, where the template no longer contains them and this
is a no-op.

Deliberately NOT ``CONCURRENTLY``. A plain ``DROP INDEX`` takes a brief
ACCESS EXCLUSIVE lock on the table, and dropping an index — unlike building one
— is a catalog update rather than a scan, so the lock is held for a moment
rather than for the length of the table. Running these inside the migration's
transaction keeps the change atomic; ``CONCURRENTLY`` cannot run in a
transaction at all.

Revision ID: b2c8d4e06f31
Revises: a1b7c3d95e24
Create Date: 2026-09-13
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

__all__ = ["branch_labels", "depends_on", "down_revision", "revision"]

revision: str = "b2c8d4e06f31"
down_revision: str | Sequence[str] | None = "a1b7c3d95e24"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: (index to drop, the twin that keeps serving the same queries). The twin is
#: recorded so a reader can check the claim without rebuilding the schema, and
#: so a future reviewer can see that nothing was dropped unpaired.
_REDUNDANT: tuple[tuple[str, str], ...] = (
    ("ix_booking_links_user_id", "ix_platform_booking_links_user_id"),
    ("ix_claim_routes_practice_id", "ix_platform_claim_routes_practice_id"),
    ("ix_companion_devices_jkt", "ix_platform_companion_devices_jkt"),
    ("ix_companion_devices_user_id", "ix_platform_companion_devices_user_id"),
    ("ix_launch_intents_expires_at", "ix_platform_launch_intents_expires_at"),
    ("ix_launch_intents_user_id", "ix_platform_launch_intents_user_id"),
    ("ix_passkey_backup_codes_user_id", "ix_platform_passkey_backup_codes_user_id"),
    ("ix_passkey_challenges_expires_at", "ix_platform_passkey_challenges_expires_at"),
    ("ix_passkey_challenges_user_id", "ix_platform_passkey_challenges_user_id"),
    ("ix_passkey_credentials_user_id", "ix_platform_passkey_credentials_user_id"),
    ("ix_platform_audit_logs_action", "ix_platform_platform_audit_logs_action"),
    ("ix_platform_audit_logs_actor", "ix_platform_platform_audit_logs_actor_user_id"),
    ("ix_platform_audit_logs_tenant_schema", "ix_platform_platform_audit_logs_tenant_schema"),
    ("ix_platform_audit_logs_timestamp", "ix_platform_platform_audit_logs_timestamp"),
    ("ix_user_identities_user_id", "ix_platform_user_identities_user_id"),
)


def upgrade() -> None:
    """Drop each redundant index, leaving its twin in place."""
    for redundant, _twin in _REDUNDANT:
        op.execute(f"DROP INDEX IF EXISTS platform.{redundant}")


def downgrade() -> None:
    """Not recreated.

    Putting a duplicate index back is not a recovery from anything — the twin
    that served these queries is still there, and was serving them the whole
    time. A downgrade that rebuilt fifteen redundant btrees would be doing real
    work to restore a defect.
    """
