# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Drop 15 duplicate platform indexes

Each of these covers a table and column list that another index already covers.
They exist because the platform schema had two builders that did not know about
each other: ``PlatformBase.metadata.create_all`` created one from the model, under
the ORM naming convention (``ix_platform_<table>_<column>``), and a migration in
this chain created its own with a hand-written name (``ix_<table>_<column>``).
``CREATE INDEX IF NOT EXISTS`` matches on *name*, so it never noticed an identical
index already standing there under the other convention.

Every duplicate costs a second btree on every insert, update and delete of the
covered column, for a read pattern one index already serves.
``platform_audit_logs`` carries four such pairs, so every audited action has been
maintaining eight indexes where four would do — on the table every audited action
writes to.

Verified against pablohealth-dev on 2026-09-14: all fifteen present, each
definition identical to its twin's, every twin also present, and no other
duplicate groups anywhere in the platform schema.

**Why this lives in the tenant chain rather than the platform chain**, given that
it is unambiguously platform DDL and the platform chain is where platform DDL
belongs now. Eight revisions in THIS chain create these indexes, and this chain
runs after the platform chain. A drop on the platform side is therefore undone a
moment later on every fresh install: the platform chain drops fifteen, the tenant
chain recreates all fifteen, and the repair only ever holds on databases that had
already run those eight revisions. Tried that way first; that is what it did.

The alternative was editing those eight revisions to stop creating them. Already
applied everywhere, so it would have changed nothing that has run — but rewriting
migrations that have executed is not a habit worth starting for a cleanup.

So it sits at the end of the chain that creates them, and leaves when they do. The
lasting fix is the one this bead is really about: platform DDL moving out of this
chain entirely, at which point this revision and its eight causes go together.

``IF EXISTS`` on every drop, and the fan-out runs this once per practice schema,
so it is executed many times and does nothing after the first.

Deliberately NOT ``CONCURRENTLY``. A plain ``DROP INDEX`` takes a brief ACCESS
EXCLUSIVE lock, and dropping an index — unlike building one — is a catalog update
rather than a scan, so the lock is held for a moment rather than for the length of
the table. Keeping it inside the migration's transaction keeps the change atomic;
``CONCURRENTLY`` cannot run in a transaction at all.

Revision ID: d8f3b6c04e17
Revises: c4d81e6a2f09
Create Date: 2026-09-14
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

__all__ = ["branch_labels", "depends_on", "down_revision", "revision"]

revision: str = "d8f3b6c04e17"
down_revision: str | Sequence[str] | None = "c4d81e6a2f09"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: (index to drop, the twin that keeps serving the same queries). The twin is
#: recorded so a reader can check the claim without rebuilding the schema, and so
#: a reviewer can see that nothing is dropped unpaired. None of the fifteen is
#: UNIQUE, and no application code names any of them.
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

    Putting a duplicate index back is not a recovery from anything — the twin that
    served these queries is still there, and was serving them the whole time. A
    downgrade that rebuilt fifteen redundant btrees would be doing real work to
    restore a defect.
    """
