# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""patient_source_mappings — remembered identifiers for every source, not only iCal

``ical_client_mappings`` kept which patient an EHR calendar feed's client
identifier ("J.A.", "SH00001") meant, once the clinician said so. The same
question comes up wherever an outside record names a client — a calendar
import, an archive — so the table is renamed to say what it holds, and its
two iCal-specific columns with it:

* ``ehr_system`` -> ``source``
* ``client_identifier`` -> ``source_identifier``

Renamed in place, so every row survives as it is. An iCal row's
``ehr_system`` value ("simplepractice", "sessions_health") is already the
right ``source``. ``doc_id`` stays the primary key: it is derived from
``user_id``, the source and the identifier, which are unchanged. The index,
primary key and foreign key are renamed to match; row policies are attached
to the table, so they follow the rename, and the tenant migrate job
reconciles them by table name after the chain runs.

A remembered answer can also be "this is not a client" — a standing staff
meeting on a calendar — so it is never asked about again. That adds
``answer`` (``'client'`` or ``'not_a_client'``, default ``'client'``, which
is what every existing row is) and makes ``patient_id`` nullable, with a
check that a patient is present exactly when the answer is a client.

``source_identifier`` and ``doc_id`` become unbounded text: a calendar
provider's series id can run to 1024 characters, past the old 255.

The downgrade has to drop the not-a-client answers, and any identifier too
long for the old columns, since the old table has nowhere to keep them. The table is FORCE-RLS'd and this chain may run as a
role without BYPASSRLS, so that delete runs with row security suspended and
restored — otherwise it would match nothing and ``SET NOT NULL`` would fail
on the rows it could not see.

Idempotent, like every revision in this chain: it is fanned out once per
practice schema, and each step is guarded on the old name still being there.

Revision ID: f4b2d8a61c73
Revises: c8a2e5f19d34
Create Date: 2026-09-30
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "f4b2d8a61c73"
down_revision: str | Sequence[str] | None = "c8a2e5f19d34"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
            IF to_regclass('ical_client_mappings') IS NOT NULL
               AND to_regclass('patient_source_mappings') IS NULL THEN
                ALTER TABLE ical_client_mappings RENAME TO patient_source_mappings;
            END IF;
            IF EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_schema = current_schema()
                  AND table_name = 'patient_source_mappings' AND column_name = 'ehr_system'
            ) THEN
                ALTER TABLE patient_source_mappings RENAME COLUMN ehr_system TO source;
            END IF;
            IF EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_schema = current_schema()
                  AND table_name = 'patient_source_mappings'
                  AND column_name = 'client_identifier'
            ) THEN
                ALTER TABLE patient_source_mappings
                    RENAME COLUMN client_identifier TO source_identifier;
            END IF;
            IF EXISTS (
                SELECT 1 FROM pg_constraint
                WHERE conrelid = to_regclass('patient_source_mappings')
                  AND conname = 'ical_client_mappings_pkey'
            ) THEN
                ALTER TABLE patient_source_mappings
                    RENAME CONSTRAINT ical_client_mappings_pkey TO patient_source_mappings_pkey;
            END IF;
            IF EXISTS (
                SELECT 1 FROM pg_constraint
                WHERE conrelid = to_regclass('patient_source_mappings')
                  AND conname = 'ical_client_mappings_patient_id_fkey'
            ) THEN
                ALTER TABLE patient_source_mappings
                    RENAME CONSTRAINT ical_client_mappings_patient_id_fkey
                    TO patient_source_mappings_patient_id_fkey;
            END IF;
            IF to_regclass('ix_ical_client_mappings_user_id') IS NOT NULL THEN
                ALTER INDEX ix_ical_client_mappings_user_id
                    RENAME TO ix_patient_source_mappings_user_id;
            END IF;
        END $$;
        """
    )
    op.execute(
        """
        ALTER TABLE patient_source_mappings
            ADD COLUMN IF NOT EXISTS answer TEXT NOT NULL DEFAULT 'client';
        ALTER TABLE patient_source_mappings ALTER COLUMN patient_id DROP NOT NULL;
        ALTER TABLE patient_source_mappings ALTER COLUMN source_identifier TYPE TEXT;
        ALTER TABLE patient_source_mappings ALTER COLUMN doc_id TYPE TEXT;
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM pg_constraint
                WHERE conrelid = to_regclass('patient_source_mappings')
                  AND conname = 'ck_patient_source_mappings_answer'
            ) THEN
                ALTER TABLE patient_source_mappings
                    ADD CONSTRAINT ck_patient_source_mappings_answer
                    CHECK (answer IN ('client', 'not_a_client'));
            END IF;
            IF NOT EXISTS (
                SELECT 1 FROM pg_constraint
                WHERE conrelid = to_regclass('patient_source_mappings')
                  AND conname = 'ck_patient_source_mappings_patient_when_client'
            ) THEN
                ALTER TABLE patient_source_mappings
                    ADD CONSTRAINT ck_patient_source_mappings_patient_when_client
                    CHECK ((answer = 'client') = (patient_id IS NOT NULL));
            END IF;
        END $$;
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DO $$
        DECLARE
            was_enabled boolean;
            was_forced boolean;
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_schema = current_schema()
                  AND table_name = 'patient_source_mappings' AND column_name = 'answer'
            ) THEN
                RETURN;
            END IF;
            SELECT relrowsecurity, relforcerowsecurity INTO was_enabled, was_forced
            FROM pg_class WHERE oid = to_regclass('patient_source_mappings');
            ALTER TABLE patient_source_mappings NO FORCE ROW LEVEL SECURITY;
            ALTER TABLE patient_source_mappings DISABLE ROW LEVEL SECURITY;
            DELETE FROM patient_source_mappings
            WHERE patient_id IS NULL
               OR length(source_identifier) > 255
               OR length(doc_id) > 500;
            IF was_enabled THEN
                ALTER TABLE patient_source_mappings ENABLE ROW LEVEL SECURITY;
            END IF;
            IF was_forced THEN
                ALTER TABLE patient_source_mappings FORCE ROW LEVEL SECURITY;
            END IF;
            ALTER TABLE patient_source_mappings
                DROP CONSTRAINT IF EXISTS ck_patient_source_mappings_patient_when_client;
            ALTER TABLE patient_source_mappings
                DROP CONSTRAINT IF EXISTS ck_patient_source_mappings_answer;
            ALTER TABLE patient_source_mappings DROP COLUMN answer;
            ALTER TABLE patient_source_mappings ALTER COLUMN patient_id SET NOT NULL;
            ALTER TABLE patient_source_mappings
                ALTER COLUMN source_identifier TYPE VARCHAR(255);
            ALTER TABLE patient_source_mappings ALTER COLUMN doc_id TYPE VARCHAR(500);
        END $$;
        """
    )
    op.execute(
        """
        DO $$
        BEGIN
            IF to_regclass('patient_source_mappings') IS NOT NULL
               AND to_regclass('ical_client_mappings') IS NULL THEN
                ALTER TABLE patient_source_mappings RENAME TO ical_client_mappings;
            END IF;
            IF EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_schema = current_schema()
                  AND table_name = 'ical_client_mappings' AND column_name = 'source'
            ) THEN
                ALTER TABLE ical_client_mappings RENAME COLUMN source TO ehr_system;
            END IF;
            IF EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_schema = current_schema()
                  AND table_name = 'ical_client_mappings'
                  AND column_name = 'source_identifier'
            ) THEN
                ALTER TABLE ical_client_mappings
                    RENAME COLUMN source_identifier TO client_identifier;
            END IF;
            IF EXISTS (
                SELECT 1 FROM pg_constraint
                WHERE conrelid = to_regclass('ical_client_mappings')
                  AND conname = 'patient_source_mappings_pkey'
            ) THEN
                ALTER TABLE ical_client_mappings
                    RENAME CONSTRAINT patient_source_mappings_pkey TO ical_client_mappings_pkey;
            END IF;
            IF EXISTS (
                SELECT 1 FROM pg_constraint
                WHERE conrelid = to_regclass('ical_client_mappings')
                  AND conname = 'patient_source_mappings_patient_id_fkey'
            ) THEN
                ALTER TABLE ical_client_mappings
                    RENAME CONSTRAINT patient_source_mappings_patient_id_fkey
                    TO ical_client_mappings_patient_id_fkey;
            END IF;
            IF to_regclass('ix_patient_source_mappings_user_id') IS NOT NULL THEN
                ALTER INDEX ix_patient_source_mappings_user_id
                    RENAME TO ix_ical_client_mappings_user_id;
            END IF;
        END $$;
        """
    )
