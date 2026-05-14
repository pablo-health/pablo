# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""patient_clinicians table + has_patient_access function + RLS-ready backfill

Adds the access-table substrate the rest of the patient-access work
hangs off:

  - ``patient_clinicians(patient_id, user_id, role, granted_at,
    granted_by, expires_at)`` — explicit per-(patient, clinician)
    grants. v1 ships with one row per existing patient (``role =
    'primary'``, backfilled from ``patients.user_id``); future
    co-treating / supervision / coverage rows are inserted at grant
    time.

  - ``app.has_patient_access(patient_id, user_id) -> bool`` — the
    single authorization predicate. Called by RLS policies and by
    the application-layer ``PatientAccess`` helper. Marked STABLE
    so PG can short-circuit repeated calls within a query.

  - Backfill from ``patients`` so every existing patient retains
    their current single owner without behavior change.

The CHECK constraint enforces the four roles defined in
``app.models.enums.ClinicianRole``. Adding a role is a one-line
migration; we did not introduce a separate roles table because the
permission interpretation (primary writes, supervisor cosigns, etc.)
is operation-context-dependent and lives in code, not in a
bitmap.

RLS policies that consume this function are created lazily by
``app.db.enable_rls_on_schema`` on every tenant-schema bootstrap —
this migration only ships the substrate. Existing tables keep their
``user_id``-column policy; tables with ``patient_id`` but no
``user_id`` (currently just ``notes``) get an access-function
policy. See the follow-up commit for the RLS update.

Notes on scope:
  - Function lives in each tenant schema (created qualified as
    ``app.has_patient_access`` only if a global ``app`` schema
    exists; otherwise unqualified in the tenant schema so RLS
    on tenant-schema tables resolves it via ``search_path``).
    Pablo's RLS bootstrap always SETs ``search_path`` to the
    tenant schema before reading, so unqualified resolution is
    correct.
  - Backfill is idempotent (``ON CONFLICT DO NOTHING``) so
    re-running across a fan-out is safe.
  - We deliberately do NOT drop ``patients.user_id`` here — it
    stays as a denormalized "primary clinician" cache and is the
    source of truth for the backfill. Dropping it is a follow-up
    cleanup once every read path has moved to
    ``patient_clinicians``.

Revision ID: 777b846ab944
Revises: c4e9a7b3f180
Create Date: 2026-05-12
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence


revision: str = "777b846ab944"
down_revision: str | Sequence[str] | None = "c4e9a7b3f180"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# Kept in lockstep with ``app.models.enums.ClinicianRole``. The CHECK
# constraint here and the StrEnum in Python must agree — there's an
# import-time assertion in db/models.py that fails the test suite if
# they drift.
_VALID_ROLES = ("primary", "co_treating", "supervisor", "covering")


def upgrade() -> None:
    role_list = ", ".join(f"'{r}'" for r in _VALID_ROLES)

    op.execute(
        f"""
        CREATE TABLE IF NOT EXISTS patient_clinicians (
            patient_id  VARCHAR(128) NOT NULL,
            user_id     VARCHAR(128) NOT NULL,
            role        VARCHAR(20)  NOT NULL DEFAULT 'primary'
                CHECK (role IN ({role_list})),
            granted_at  TIMESTAMPTZ  NOT NULL DEFAULT now(),
            granted_by  VARCHAR(128) NOT NULL,
            expires_at  TIMESTAMPTZ,
            PRIMARY KEY (patient_id, user_id),
            FOREIGN KEY (patient_id) REFERENCES patients(id) ON DELETE CASCADE
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_patient_clinicians_user_id "
        "ON patient_clinicians (user_id)"
    )

    # Single authorization predicate. STABLE so PG can short-circuit
    # repeated calls within a query plan. SECURITY DEFINER is NOT used
    # — we want the function to respect RLS so the same predicate works
    # as a defense-in-depth check from application code.
    op.execute(
        """
        CREATE OR REPLACE FUNCTION has_patient_access(
            p_patient_id VARCHAR,
            p_user_id    VARCHAR
        ) RETURNS BOOLEAN
        LANGUAGE sql
        STABLE
        AS $$
            SELECT EXISTS (
                SELECT 1 FROM patient_clinicians
                WHERE patient_id = p_patient_id
                  AND user_id    = p_user_id
                  AND (expires_at IS NULL OR expires_at > now())
            );
        $$
        """
    )

    # Backfill: every existing patient gets a 'primary' grant for its
    # current owner. ON CONFLICT DO NOTHING makes this safe to re-run
    # across the per-tenant fan-out and idempotent on tenant
    # reconciliation.
    op.execute(
        """
        INSERT INTO patient_clinicians (patient_id, user_id, role, granted_by)
        SELECT id, user_id, 'primary', user_id FROM patients
        ON CONFLICT (patient_id, user_id) DO NOTHING
        """
    )


def downgrade() -> None:
    op.execute("DROP FUNCTION IF EXISTS has_patient_access(VARCHAR, VARCHAR)")
    op.execute("DROP TABLE IF EXISTS patient_clinicians")
