# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Convert user_id (and actor) String(128) columns to native UUID — Phase C.

``b7e25c1d8a4f`` converted every uuid4-shaped ``id`` / ``patient_id``
column to native ``UUID`` but deliberately deferred ``*.user_id`` to
"Phase C" because at the time it still held the raw Firebase uid. It no
longer does: ``app/repositories/postgres/identity.py`` generates the
canonical ``user_id`` as ``str(uuid.uuid4())`` and the external auth
subject lives separately in ``platform.user_identities.subject_id``. So
every column that holds a ``user_id`` becomes native ``uuid`` here —
16 bytes vs 37, compact indexes, and the DB rejects malformed values.

Scope — every column holding a user_id, across both schemas:

  per-tenant practice schema
    clinician_profiles.user_id              patient_documents.user_id
    therapy_sessions.user_id                appointments.user_id
    notes.export_reviewed_by                availability_rules.user_id
    patient_clinicians.user_id, .granted_by google_calendar_tokens.user_id
    outcome_measures.created_by             ical_client_mappings.user_id
    patient_medications.created_by          ical_sync_configs.user_id
    diagnostic_assessments.created_by       supervision_relationships.user_id
    compliance_items.user_id                supervision_hours.user_id
    compliance_documents.uploaded_by_user_id llm_usage.user_id
    chat_conversations.owner_user_id
    prescribing_encounters.prescriber_user_id, .finalized_by, .created_by
    prescriptions.created_by                prescribing_encounter_addenda.created_by
    prescribing_checklist_items.captured_by, .created_by

  shared platform schema
    users.id                                user_preferences.user_id
    user_identities.user_id                 companion_devices.user_id
    practices.owner_user_id

Deliberately NOT converted (kept VARCHAR):

  audit_logs.user_id / platform_audit_logs.actor_user_id — the audit
      tables record the actor identifier *as recorded*. A forensic log
      must capture the event even when the actor isn't a clean uuid4
      (system/service actions, legacy ids, an unauthenticated probe
      logged precisely because it was suspicious); a uuid column would
      reject those at INSERT and lose the record. Capture beats type
      tidiness here — same call as the polymorphic resource_id below.
  audit_logs.resource_id / platform_audit_logs.resource_id — polymorphic,
      hold a user_id, patient_id, or session_id depending on the action.
  user_identities.subject_id — the external auth uid (Firebase 28-char,
      Auth0 ~40, …), never a uuid.
  prescribing_encounters.delegation_ref — free-form agreement pointer.
  allowed_emails.added_by / ehr_prompts.updated_by — emails.

RLS interaction. ``user_id`` columns are referenced directly in RLS
policies as ``(user_id)::text = current_setting('app.current_user_id',
true)``. Postgres refuses ``ALTER COLUMN ... TYPE`` while a policy
references the column (the constraint ``9dea1edf7fe0`` hit dropping
``patients.user_id``), so each affected table's policies are saved,
dropped, the columns altered, then the policies restored verbatim. The
stored ``(user_id)::text`` form stays valid against a ``uuid`` column —
``uuid::text`` is a defined cast — so the restore needs no rewrite, and
restoring only what was present means the template (which carries just
``rls_patient_doc_access``) gains no policy that ``enable_rls_on_schema``
is supposed to own. The restored policy is byte-identical to what the
updated ``enable_rls_on_schema`` now emits, so existing tenants, freshly
provisioned tenants, and the template all converge.

has_patient_access. Its body ``user_id = p_user_id`` becomes
``uuid = character varying`` once ``patient_clinicians.user_id`` is
``uuid`` — the exact ``operator does not exist`` failure
``c8a31f6e2d54`` fixed for ``patient_id``. The body is updated in
lockstep to ``user_id::text = p_user_id``; the ``(UUID, VARCHAR)``
signature is unchanged, so the RLS USING-clauses (which pass the text
GUC) and the repo callers (which bind ``:uid`` as text) keep resolving.

Data. Every existing value must be a valid uuid4 string — a row still
holding a legacy Firebase uid aborts the ``::uuid`` cast (verify before
applying to a schema with pre-uuid4 rows). ``practices.owner_user_id``
carried a ``''`` sentinel (app-side ``default=""``); it becomes a
nullable ``uuid`` with ``''`` mapped to ``NULL``.

FK constraints touching converted columns are dropped and recreated via
``pg_get_constraintdef`` (``companion_devices.user_id`` -> ``users.id``,
plus any per-tenant FK on an affected table), same mechanism as
``b7e25c1d8a4f``.

Idempotent under the per-tenant fan-out: every ALTER is guarded on the
column still being ``character varying``; policy and FK save/restore use
``IF EXISTS`` / existence checks.

Revision ID: c1d7e4a9f2b6
Revises: f4c1a9d3b7e2
Create Date: 2026-06-08
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

__all__ = ["branch_labels", "depends_on", "down_revision", "revision"]

revision: str = "c1d7e4a9f2b6"
down_revision: str | Sequence[str] | None = "f4c1a9d3b7e2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# (table, column) pairs in the per-tenant practice schema.
TENANT_COLUMNS: list[tuple[str, str]] = [
    ("clinician_profiles", "user_id"),
    ("therapy_sessions", "user_id"),
    ("notes", "export_reviewed_by"),
    ("patient_clinicians", "user_id"),
    ("patient_clinicians", "granted_by"),
    ("outcome_measures", "created_by"),
    ("patient_medications", "created_by"),
    ("diagnostic_assessments", "created_by"),
    ("appointments", "user_id"),
    ("availability_rules", "user_id"),
    ("google_calendar_tokens", "user_id"),
    ("ical_client_mappings", "user_id"),
    ("ical_sync_configs", "user_id"),
    ("compliance_items", "user_id"),
    ("compliance_documents", "uploaded_by_user_id"),
    ("supervision_relationships", "user_id"),
    ("supervision_hours", "user_id"),
    ("chat_conversations", "owner_user_id"),
    ("llm_usage", "user_id"),
    ("patient_documents", "user_id"),
    ("prescribing_encounters", "prescriber_user_id"),
    ("prescribing_encounters", "finalized_by"),
    ("prescribing_encounters", "created_by"),
    ("prescriptions", "created_by"),
    ("prescribing_encounter_addenda", "created_by"),
    ("prescribing_checklist_items", "captured_by"),
    ("prescribing_checklist_items", "created_by"),
]

# (schema, table, column) in the shared platform schema. ``owner_user_id``
# is handled separately (it drops NOT NULL + maps '' -> NULL).
PLATFORM_COLUMNS: list[tuple[str, str, str]] = [
    ("platform", "users", "id"),
    ("platform", "user_identities", "user_id"),
    ("platform", "user_preferences", "user_id"),
    ("platform", "companion_devices", "user_id"),
]


def _affected_tenant_tables() -> list[str]:
    # Stable, de-duplicated table order for the SQL IN-lists.
    seen: dict[str, None] = {}
    for table, _ in TENANT_COLUMNS:
        seen.setdefault(table, None)
    return list(seen)


def _quoted_list(values: list[str]) -> str:
    return ", ".join(f"'{v}'" for v in values)


# --------------------------------------------------------------------------
# Column type flips. ``NULLIF(col, '')`` maps the practices.owner_user_id
# sentinel (and any stray '') to NULL; a real uuid4 string is unchanged, and
# a legacy non-uuid value aborts the cast (intended — see the data note).
# --------------------------------------------------------------------------
def _alter_to_uuid_current(table: str, column: str) -> None:
    op.execute(
        f"""DO $$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_schema = current_schema()
                  AND table_name = '{table}'
                  AND column_name = '{column}'
                  AND data_type = 'character varying'
            ) THEN
                EXECUTE 'ALTER TABLE {table}
                         ALTER COLUMN {column} TYPE uuid
                         USING NULLIF({column}, '''')::uuid';
            END IF;
        END $$;"""  # noqa: S608
    )


def _alter_to_uuid_schema(schema: str, table: str, column: str) -> None:
    op.execute(
        f"""DO $$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_schema = '{schema}'
                  AND table_name = '{table}'
                  AND column_name = '{column}'
                  AND data_type = 'character varying'
            ) THEN
                EXECUTE 'ALTER TABLE {schema}.{table}
                         ALTER COLUMN {column} TYPE uuid
                         USING NULLIF({column}, '''')::uuid';
            END IF;
        END $$;"""  # noqa: S608
    )


def _alter_to_varchar_current(table: str, column: str) -> None:
    op.execute(
        f"""DO $$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_schema = current_schema()
                  AND table_name = '{table}'
                  AND column_name = '{column}'
                  AND data_type = 'uuid'
            ) THEN
                EXECUTE 'ALTER TABLE {table}
                         ALTER COLUMN {column} TYPE varchar(128)
                         USING {column}::text';
            END IF;
        END $$;"""  # noqa: S608
    )


def _alter_to_varchar_schema(schema: str, table: str, column: str) -> None:
    op.execute(
        f"""DO $$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_schema = '{schema}'
                  AND table_name = '{table}'
                  AND column_name = '{column}'
                  AND data_type = 'uuid'
            ) THEN
                EXECUTE 'ALTER TABLE {schema}.{table}
                         ALTER COLUMN {column} TYPE varchar(128)
                         USING {column}::text';
            END IF;
        END $$;"""  # noqa: S608
    )


# --------------------------------------------------------------------------
# RLS policy save / drop / restore for the current (tenant) schema. Restore
# is verbatim from pg_policies, so a ``(user_id)::text = …`` policy comes
# back unchanged and now compares the uuid column cast to text.
# --------------------------------------------------------------------------
def _save_and_drop_policies_current() -> None:
    table_list = _quoted_list(_affected_tenant_tables())
    op.execute(
        f"""
    DROP TABLE IF EXISTS _phasec_saved_policies;
    CREATE TEMP TABLE _phasec_saved_policies (
        schema_name text NOT NULL,
        table_name  text NOT NULL,
        policy_name text NOT NULL,
        cmd         text NOT NULL,
        permissive  text NOT NULL,
        roles       text[] NOT NULL,
        qual        text,
        with_check  text
    );

    INSERT INTO _phasec_saved_policies
        (schema_name, table_name, policy_name, cmd, permissive, roles, qual, with_check)
    SELECT schemaname, tablename, policyname, cmd, permissive, roles, qual, with_check
    FROM pg_policies
    WHERE schemaname = current_schema()
      AND tablename IN ({table_list});

    DO $$
    DECLARE r RECORD;
    BEGIN
        FOR r IN SELECT * FROM _phasec_saved_policies LOOP
            EXECUTE format('DROP POLICY IF EXISTS %I ON %I.%I',
                           r.policy_name, r.schema_name, r.table_name);
        END LOOP;
    END $$;
    """  # noqa: S608  -- table names are module-level constants
    )


def _restore_policies_current() -> None:
    op.execute(
        """
    DO $$
    DECLARE r RECORD; stmt text;
    BEGIN
        IF to_regclass('_phasec_saved_policies') IS NULL THEN
            RETURN;
        END IF;
        FOR r IN SELECT * FROM _phasec_saved_policies LOOP
            stmt := format('CREATE POLICY %I ON %I.%I AS %s FOR %s',
                           r.policy_name, r.schema_name, r.table_name,
                           r.permissive, r.cmd);
            -- Our policies are created without a TO clause (PUBLIC). Only
            -- emit one if a saved policy was scoped to specific roles.
            IF r.roles IS DISTINCT FROM ARRAY['public']::text[] THEN
                stmt := stmt || ' TO ' || array_to_string(r.roles, ', ');
            END IF;
            IF r.qual IS NOT NULL THEN
                stmt := stmt || ' USING (' || r.qual || ')';
            END IF;
            IF r.with_check IS NOT NULL THEN
                stmt := stmt || ' WITH CHECK (' || r.with_check || ')';
            END IF;
            EXECUTE stmt;
        END LOOP;
    END $$;
    DROP TABLE IF EXISTS _phasec_saved_policies;
    """
    )


# --------------------------------------------------------------------------
# FK save / drop / restore (drop before altering a referenced/ing column;
# pg_get_constraintdef so we don't hardcode an FK list). Mirrors b7e25c1d8a4f.
# --------------------------------------------------------------------------
def _save_and_drop_fks_current() -> None:
    table_list = _quoted_list(_affected_tenant_tables())
    op.execute(
        f"""
    DROP TABLE IF EXISTS _phasec_saved_fks;
    CREATE TEMP TABLE _phasec_saved_fks (
        schema_name text NOT NULL,
        table_name text NOT NULL,
        constraint_name text NOT NULL,
        constraint_def text NOT NULL
    );

    INSERT INTO _phasec_saved_fks (schema_name, table_name, constraint_name, constraint_def)
    SELECT n.nspname, c.relname, con.conname, pg_get_constraintdef(con.oid)
    FROM pg_constraint con
    JOIN pg_class c  ON con.conrelid  = c.oid
    JOIN pg_namespace n ON c.relnamespace = n.oid
    LEFT JOIN pg_class rc ON con.confrelid = rc.oid
    WHERE con.contype = 'f'
      AND n.nspname = current_schema()
      AND (c.relname IN ({table_list}) OR rc.relname IN ({table_list}));

    DO $$
    DECLARE r RECORD;
    BEGIN
        FOR r IN SELECT * FROM _phasec_saved_fks LOOP
            EXECUTE format('ALTER TABLE %I.%I DROP CONSTRAINT %I',
                           r.schema_name, r.table_name, r.constraint_name);
        END LOOP;
    END $$;
    """  # noqa: S608  -- table names are module-level constants
    )


def _restore_fks_current() -> None:
    op.execute(
        """
    DO $$
    DECLARE r RECORD;
    BEGIN
        IF to_regclass('_phasec_saved_fks') IS NULL THEN
            RETURN;
        END IF;
        FOR r IN SELECT * FROM _phasec_saved_fks LOOP
            EXECUTE format('ALTER TABLE %I.%I ADD CONSTRAINT %I %s',
                           r.schema_name, r.table_name,
                           r.constraint_name, r.constraint_def);
        END LOOP;
    END $$;
    DROP TABLE IF EXISTS _phasec_saved_fks;
    """
    )


def _save_and_drop_fks_platform() -> None:
    schemas = sorted({s for s, _, _ in PLATFORM_COLUMNS} | {"platform"})
    tables = sorted({t for _, t, _ in PLATFORM_COLUMNS} | {"practices"})
    schema_list = _quoted_list(schemas)
    table_list = _quoted_list(tables)
    op.execute(
        f"""
    DROP TABLE IF EXISTS _phasec_saved_platform_fks;
    CREATE TEMP TABLE _phasec_saved_platform_fks (
        schema_name text NOT NULL,
        table_name text NOT NULL,
        constraint_name text NOT NULL,
        constraint_def text NOT NULL
    );

    INSERT INTO _phasec_saved_platform_fks
        (schema_name, table_name, constraint_name, constraint_def)
    SELECT n.nspname, c.relname, con.conname, pg_get_constraintdef(con.oid)
    FROM pg_constraint con
    JOIN pg_class c  ON con.conrelid  = c.oid
    JOIN pg_namespace n ON c.relnamespace = n.oid
    LEFT JOIN pg_class rc ON con.confrelid = rc.oid
    LEFT JOIN pg_namespace rn ON rc.relnamespace = rn.oid
    WHERE con.contype = 'f'
      AND n.nspname IN ({schema_list})
      AND (
          c.relname IN ({table_list})
          OR (rc.relname IS NOT NULL
              AND rn.nspname IN ({schema_list})
              AND rc.relname IN ({table_list}))
      );

    DO $$
    DECLARE r RECORD;
    BEGIN
        FOR r IN SELECT * FROM _phasec_saved_platform_fks LOOP
            EXECUTE format('ALTER TABLE %I.%I DROP CONSTRAINT %I',
                           r.schema_name, r.table_name, r.constraint_name);
        END LOOP;
    END $$;
    """  # noqa: S608  -- identifiers are module-level constants
    )


def _restore_fks_platform() -> None:
    op.execute(
        """
    DO $$
    DECLARE r RECORD;
    BEGIN
        IF to_regclass('_phasec_saved_platform_fks') IS NULL THEN
            RETURN;
        END IF;
        FOR r IN SELECT * FROM _phasec_saved_platform_fks LOOP
            EXECUTE format('ALTER TABLE %I.%I ADD CONSTRAINT %I %s',
                           r.schema_name, r.table_name,
                           r.constraint_name, r.constraint_def);
        END LOOP;
    END $$;
    DROP TABLE IF EXISTS _phasec_saved_platform_fks;
    """
    )


# --------------------------------------------------------------------------
# has_patient_access body — keep the (UUID, VARCHAR) signature, cast the
# now-uuid user_id column to text for the comparison against the text arg.
# --------------------------------------------------------------------------
_HAS_PATIENT_ACCESS_UUID_BODY = """
    CREATE OR REPLACE FUNCTION has_patient_access(
        p_patient_id UUID,
        p_user_id    VARCHAR
    ) RETURNS BOOLEAN
    LANGUAGE sql
    STABLE
    AS $$
        SELECT EXISTS (
            SELECT 1 FROM patient_clinicians
            WHERE patient_id = p_patient_id
              AND user_id::text = p_user_id
              AND (expires_at IS NULL OR expires_at > now())
        );
    $$
"""

_HAS_PATIENT_ACCESS_VARCHAR_BODY = """
    CREATE OR REPLACE FUNCTION has_patient_access(
        p_patient_id UUID,
        p_user_id    VARCHAR
    ) RETURNS BOOLEAN
    LANGUAGE sql
    STABLE
    AS $$
        SELECT EXISTS (
            SELECT 1 FROM patient_clinicians
            WHERE patient_id = p_patient_id
              AND user_id = p_user_id
              AND (expires_at IS NULL OR expires_at > now())
        );
    $$
"""


def upgrade() -> None:
    # Per-tenant fan-out runs this with search_path = <tenant>, platform,
    # public — current_schema() is the tenant (or the 'practice' template on
    # the deploy-time default path).
    _save_and_drop_policies_current()
    _save_and_drop_fks_current()
    for table, column in TENANT_COLUMNS:
        _alter_to_uuid_current(table, column)
    _restore_fks_current()
    _restore_policies_current()
    # The function only exists once patient_clinicians does; CREATE OR REPLACE
    # is a no-op-safe rewrite on every fan-out invocation.
    op.execute(_HAS_PATIENT_ACCESS_UUID_BODY)

    # Platform schema (idempotent across the per-tenant fan-out).
    _save_and_drop_fks_platform()
    for schema, table, column in PLATFORM_COLUMNS:
        _alter_to_uuid_schema(schema, table, column)
    # practices.owner_user_id: drop the NOT NULL, map '' sentinel to NULL.
    op.execute(
        """DO $$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_schema = 'platform'
                  AND table_name = 'practices'
                  AND column_name = 'owner_user_id'
                  AND data_type = 'character varying'
            ) THEN
                EXECUTE 'ALTER TABLE platform.practices
                         ALTER COLUMN owner_user_id DROP NOT NULL';
                -- Drop any server DEFAULT first: ALTER TYPE re-casts the
                -- default expression, and a leftover ''::uuid (a drifted
                -- DB or the legacy '' sentinel) is an invalid uuid.
                EXECUTE 'ALTER TABLE platform.practices
                         ALTER COLUMN owner_user_id DROP DEFAULT';
                EXECUTE 'ALTER TABLE platform.practices
                         ALTER COLUMN owner_user_id TYPE uuid
                         USING NULLIF(owner_user_id, '''')::uuid';
            END IF;
        END $$;"""
    )
    _restore_fks_platform()


def downgrade() -> None:
    # Reverse: platform first (so owner_user_id / FKs revert before tenants),
    # then per-tenant.
    _save_and_drop_fks_platform()
    op.execute(
        """DO $$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_schema = 'platform'
                  AND table_name = 'practices'
                  AND column_name = 'owner_user_id'
                  AND data_type = 'uuid'
            ) THEN
                EXECUTE 'ALTER TABLE platform.practices
                         ALTER COLUMN owner_user_id TYPE varchar(128)
                         USING COALESCE(owner_user_id::text, '''')';
                EXECUTE 'UPDATE platform.practices
                         SET owner_user_id = '''' WHERE owner_user_id IS NULL';
                EXECUTE 'ALTER TABLE platform.practices
                         ALTER COLUMN owner_user_id SET NOT NULL';
            END IF;
        END $$;"""
    )
    for schema, table, column in reversed(PLATFORM_COLUMNS):
        _alter_to_varchar_schema(schema, table, column)
    _restore_fks_platform()

    _save_and_drop_policies_current()
    _save_and_drop_fks_current()
    for table, column in reversed(TENANT_COLUMNS):
        _alter_to_varchar_current(table, column)
    _restore_fks_current()
    _restore_policies_current()
    # Recreate the function AFTER patient_clinicians.user_id is back to
    # varchar — the varchar body (``user_id = p_user_id``) would fail
    # body validation while the column is still uuid.
    op.execute(_HAS_PATIENT_ACCESS_VARCHAR_BODY)
