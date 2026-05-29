# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Move BAA snapshot to the practice; split professional credentials

The BAA is between Pablo and the covered entity (the practice), not the
individual clinician. This migration relocates the BAA snapshot
accordingly and splits the professional-credential fields to their
natural owners:

- ``platform.practices`` gains ``address`` plus the BAA snapshot
  (``baa_accepted_at``, ``baa_version``, ``baa_legal_name``,
  ``baa_license_number``, ``baa_license_state``, ``baa_practice_name``,
  ``baa_business_address``, ``baa_full_text``).
- ``platform.users`` gains ``legal_name`` (a person attribute, used to
  pre-fill the BAA signer block) and DROPS the six moved BAA columns.
  ``baa_accepted_at`` / ``baa_version`` STAY on the user row as the fast
  per-request gate read by ``require_baa_acceptance``.
- ``clinician_profiles`` (per-tenant) gains ``license_number`` /
  ``license_state`` — professional credentials alongside title.

Data migration: existing rows are migrated under the
solo-therapist assumption (one practice ↔ one therapist). Each user is
mapped to their practice via ``email_tenant_mappings`` and their BAA
snapshot is copied onto that practice row; ``legal_name`` is backfilled
from ``baa_legal_name``; ``practices.address`` is backfilled from
``baa_business_address``. The copy + column-drop are guarded by a
column-existence check so the statement is safe to re-run under the
per-tenant migration fan-out (first pass copies + drops; later passes
see the columns gone and skip).

Revision ID: c3f8b1d92e47
Revises: f3a9c1e84b27
Create Date: 2026-05-29
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "c3f8b1d92e47"
down_revision: str | Sequence[str] | None = "f3a9c1e84b27"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # ── Platform: practices gains address + BAA snapshot ───────────────
    # Idempotent (IF NOT EXISTS): platform.practices is cross-tenant but
    # the chain re-runs once per practice schema during fan-out.
    op.execute(
        "ALTER TABLE platform.practices "
        "ADD COLUMN IF NOT EXISTS address VARCHAR(500)"
    )
    for col, coltype in (
        ("baa_accepted_at", "TIMESTAMPTZ"),
        ("baa_version", "VARCHAR(10)"),
        ("baa_legal_name", "VARCHAR(255)"),
        ("baa_license_number", "VARCHAR(100)"),
        ("baa_license_state", "VARCHAR(2)"),
        ("baa_practice_name", "VARCHAR(255)"),
        ("baa_business_address", "VARCHAR(500)"),
        ("baa_full_text", "TEXT"),
    ):
        op.execute(
            f"ALTER TABLE platform.practices ADD COLUMN IF NOT EXISTS {col} {coltype}"
        )

    # ── Platform: users gains legal_name ───────────────────────────────
    op.execute(
        "ALTER TABLE platform.users ADD COLUMN IF NOT EXISTS legal_name VARCHAR(255)"
    )

    # ── Per-tenant: clinician_profiles gains license_* ─────────────────
    # Unqualified name resolves via search_path to the active practice
    # schema (template at deploy time, each tenant during fan-out).
    op.execute(
        "ALTER TABLE clinician_profiles ADD COLUMN IF NOT EXISTS license_number VARCHAR(100)"
    )
    op.execute(
        "ALTER TABLE clinician_profiles ADD COLUMN IF NOT EXISTS license_state VARCHAR(2)"
    )

    # ── Data migration + column drop (guarded, fan-out-safe) ───────────
    # Solo-therapist assumption: one practice ↔ one therapist, mapped via
    # email_tenant_mappings. Runs in full only while the legacy columns
    # still exist; once dropped, the guard short-circuits the whole block.
    op.execute(
        """
        DO $$
        BEGIN
          IF EXISTS (
            SELECT 1 FROM information_schema.columns
            WHERE table_schema = 'platform'
              AND table_name = 'users'
              AND column_name = 'baa_legal_name'
          ) THEN
            -- legal_name is a straight rename from baa_legal_name.
            UPDATE platform.users
               SET legal_name = baa_legal_name
             WHERE legal_name IS NULL
               AND baa_legal_name IS NOT NULL;

            -- Snapshot the BAA onto the owning practice (1:1 via mapping).
            UPDATE platform.practices p
               SET baa_accepted_at      = u.baa_accepted_at,
                   baa_version          = u.baa_version,
                   baa_legal_name       = u.baa_legal_name,
                   baa_license_number   = u.baa_license_number,
                   baa_license_state    = u.baa_license_state,
                   baa_practice_name    = COALESCE(u.baa_practice_name, p.name),
                   baa_business_address = u.baa_business_address,
                   baa_full_text        = u.baa_full_text,
                   address              = COALESCE(p.address, u.baa_business_address)
              FROM platform.email_tenant_mappings m
              JOIN platform.users u ON u.email = m.email
             WHERE m.practice_id = p.id
               AND p.baa_accepted_at IS NULL
               AND u.baa_accepted_at IS NOT NULL;

            -- Drop the moved columns from users (gate columns stay).
            ALTER TABLE platform.users
              DROP COLUMN IF EXISTS baa_legal_name,
              DROP COLUMN IF EXISTS baa_license_number,
              DROP COLUMN IF EXISTS baa_license_state,
              DROP COLUMN IF EXISTS baa_practice_name,
              DROP COLUMN IF EXISTS baa_business_address,
              DROP COLUMN IF EXISTS baa_full_text;
          END IF;
        END $$;
        """
    )


def downgrade() -> None:
    # Re-add the moved columns to users, copy back from the practice
    # snapshot (1:1), then drop the practice/clinician additions.
    op.execute(
        "ALTER TABLE platform.users ADD COLUMN IF NOT EXISTS baa_legal_name VARCHAR(255)"
    )
    op.execute(
        "ALTER TABLE platform.users ADD COLUMN IF NOT EXISTS baa_license_number VARCHAR(100)"
    )
    op.execute(
        "ALTER TABLE platform.users ADD COLUMN IF NOT EXISTS baa_license_state VARCHAR(2)"
    )
    op.execute(
        "ALTER TABLE platform.users ADD COLUMN IF NOT EXISTS baa_practice_name VARCHAR(255)"
    )
    op.execute(
        "ALTER TABLE platform.users ADD COLUMN IF NOT EXISTS baa_business_address VARCHAR(500)"
    )
    op.execute("ALTER TABLE platform.users ADD COLUMN IF NOT EXISTS baa_full_text TEXT")

    op.execute(
        """
        UPDATE platform.users u
           SET baa_legal_name       = COALESCE(u.legal_name, p.baa_legal_name),
               baa_license_number   = p.baa_license_number,
               baa_license_state    = p.baa_license_state,
               baa_practice_name    = p.baa_practice_name,
               baa_business_address = p.baa_business_address,
               baa_full_text        = p.baa_full_text
          FROM platform.email_tenant_mappings m
          JOIN platform.practices p ON p.id = m.practice_id
         WHERE m.email = u.email
           AND p.baa_accepted_at IS NOT NULL;
        """
    )

    op.execute("ALTER TABLE clinician_profiles DROP COLUMN IF EXISTS license_state")
    op.execute("ALTER TABLE clinician_profiles DROP COLUMN IF EXISTS license_number")

    op.execute("ALTER TABLE platform.users DROP COLUMN IF EXISTS legal_name")

    for col in (
        "baa_full_text",
        "baa_business_address",
        "baa_practice_name",
        "baa_license_state",
        "baa_license_number",
        "baa_legal_name",
        "baa_version",
        "baa_accepted_at",
        "address",
    ):
        op.execute(f"ALTER TABLE platform.practices DROP COLUMN IF EXISTS {col}")
