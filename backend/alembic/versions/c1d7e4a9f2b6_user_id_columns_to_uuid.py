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

Data. ``a4c91b6e3f08`` linked every pre-existing user as ``('firebase',
<uid>, <uid>)`` — a legacy account's ``users.id`` IS its Firebase uid,
not a uuid4 string, and the ``::uuid`` cast aborts on it (this bit a
real deployment). So before any cast, legacy ids are remapped: each
non-uuid ``platform.users.id`` gets a fresh ``gen_random_uuid()``,
propagated to every platform column holding a user_id, and — via the
unchanged ``user_identities.subject_id``, which is how the user
authenticates, so logins keep resolving — to the per-tenant ``user_id``
columns during fan-out. The audit-log columns keep the as-recorded
legacy id on purpose (see above); it stays resolvable through
``user_identities.subject_id``. A non-uuid value with no identity
mapping still aborts the cast — better loud than silently NULLed. The
remap is not reversed on downgrade (the new ids are valid in a varchar
column). ``practices.owner_user_id`` carried a ``''`` sentinel
(app-side ``default=""``); it becomes a nullable ``uuid`` with ``''``
mapped to ``NULL``.

FK constraints touching converted columns are dropped and recreated via
``pg_get_constraintdef`` (``companion_devices.user_id`` -> ``users.id``,
plus any per-tenant FK on an affected table), same mechanism as
``b7e25c1d8a4f``. The platform snapshot intentionally also catches FKs
from tables this file does not know about — a deployment can define
extra tables referencing ``users(id)``, and those FKs must come down
before the cast regardless. Their referencing columns are then flipped
to ``uuid`` (with the same legacy-id remap) before the FK restore,
driven by the snapshot itself rather than a hardcoded list — restoring
a varchar column's FK against the now-uuid ``users.id`` would fail with
"incompatible types" (this bit a real deployment).

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


# Anchored uuid shape — anything not matching is a legacy id.
_UUID_RE = "^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"


# --------------------------------------------------------------------------
# Legacy-id remap (see the "Data" note). Runs BEFORE any cast, after the
# platform FKs are down (they reference users.id). Guarded on users.id still
# being varchar so per-tenant fan-out replays are no-ops, and a plain UPDATE
# inside the guard never name-resolves when skipped.
# --------------------------------------------------------------------------
def _remap_legacy_platform_user_ids() -> None:
    op.execute(
        f"""DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_schema = 'platform'
                  AND table_name = 'users'
                  AND column_name = 'id'
                  AND data_type = 'character varying'
            ) THEN
                RETURN;
            END IF;

            CREATE TEMP TABLE _phasec_legacy_ids AS
            SELECT id AS old_id, gen_random_uuid()::text AS new_id
            FROM platform.users
            WHERE id !~ '{_UUID_RE}';

            -- subject_id (the external auth uid) is deliberately untouched:
            -- it is the login key that keeps resolving to the new user_id.
            UPDATE platform.user_identities ui SET user_id = m.new_id
                FROM _phasec_legacy_ids m WHERE ui.user_id = m.old_id;
            UPDATE platform.user_preferences p SET user_id = m.new_id
                FROM _phasec_legacy_ids m WHERE p.user_id = m.old_id;
            UPDATE platform.companion_devices d SET user_id = m.new_id
                FROM _phasec_legacy_ids m WHERE d.user_id = m.old_id;
            UPDATE platform.practices pr SET owner_user_id = m.new_id
                FROM _phasec_legacy_ids m WHERE pr.owner_user_id = m.old_id;
            UPDATE platform.users u SET id = m.new_id
                FROM _phasec_legacy_ids m WHERE u.id = m.old_id;

            DROP TABLE _phasec_legacy_ids;
        END $$;"""  # noqa: S608
    )


def _remap_legacy_tenant_user_ids() -> None:
    # Per-tenant values hold the same legacy id the platform side just
    # remapped; user_identities (subject_id = the legacy id) carries the
    # old -> new mapping across fan-out invocations. ``user_id::text`` is
    # valid whether the platform cast has already run (uuid) or not yet
    # (varchar, same upgrade() invocation on a single-schema install).
    for table, column in TENANT_COLUMNS:
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
                    UPDATE {table} t SET {column} = ui.user_id::text
                    FROM platform.user_identities ui
                    WHERE ui.provider = 'firebase'
                      AND ui.subject_id = t.{column}
                      AND t.{column} !~ '{_UUID_RE}';
                END IF;
            END $$;"""  # noqa: S608
        )


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
        constraint_def text NOT NULL,
        ref_schema text,
        ref_table text,
        src_columns text[] NOT NULL
    );

    INSERT INTO _phasec_saved_platform_fks
        (schema_name, table_name, constraint_name, constraint_def,
         ref_schema, ref_table, src_columns)
    SELECT n.nspname, c.relname, con.conname, pg_get_constraintdef(con.oid),
           rn.nspname, rc.relname,
           (SELECT array_agg(att.attname ORDER BY u.ord)
            FROM unnest(con.conkey) WITH ORDINALITY AS u(attnum, ord)
            JOIN pg_attribute att
              ON att.attrelid = con.conrelid AND att.attnum = u.attnum)
    FROM pg_constraint con
    JOIN pg_class c  ON con.conrelid  = c.oid
    JOIN pg_namespace n ON c.relnamespace = n.oid
    LEFT JOIN pg_class rc ON con.confrelid = rc.oid
    LEFT JOIN pg_namespace rn ON rc.relnamespace = rn.oid
    WHERE con.contype = 'f'
      AND (
          (n.nspname IN ({schema_list}) AND c.relname IN ({table_list}))
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
# Columns OUTSIDE the hardcoded lists that reference users(id) via an FK —
# tables a deployment defines on top of the engine schema. The FK snapshot
# above already catches them (anything referencing a listed table comes
# down before the cast); their columns must flip in lockstep with users.id
# or the FK restore fails with "incompatible types: character varying and
# uuid". Discovered from the snapshot itself, so nothing is hardcoded.
# --------------------------------------------------------------------------
def _convert_snapshotted_user_ref_columns() -> None:
    op.execute(
        f"""DO $$
        DECLARE
            r RECORD;
            col text;
            uuid_re CONSTANT text := '{_UUID_RE}';
        BEGIN
            IF to_regclass('_phasec_saved_platform_fks') IS NULL THEN
                RETURN;
            END IF;
            FOR r IN
                SELECT * FROM _phasec_saved_platform_fks
                WHERE ref_schema = 'platform' AND ref_table = 'users'
            LOOP
                FOREACH col IN ARRAY r.src_columns LOOP
                    IF EXISTS (
                        SELECT 1 FROM information_schema.columns
                        WHERE table_schema = r.schema_name
                          AND table_name = r.table_name
                          AND column_name = col
                          AND data_type = 'character varying'
                    ) THEN
                        -- Same legacy-id remap as the listed columns: the
                        -- old -> new mapping rides user_identities
                        -- (subject_id keeps the external uid).
                        EXECUTE format(
                            'UPDATE %I.%I t SET %I = ui.user_id::text
                             FROM platform.user_identities ui
                             WHERE ui.provider = ''firebase''
                               AND ui.subject_id = t.%I
                               AND t.%I !~ %L',
                            r.schema_name, r.table_name, col, col, col, uuid_re);
                        EXECUTE format(
                            'ALTER TABLE %I.%I ALTER COLUMN %I TYPE uuid
                             USING NULLIF(%I, '''')::uuid',
                            r.schema_name, r.table_name, col, col);
                    END IF;
                END LOOP;
            END LOOP;
        END $$;"""  # noqa: S608
    )


def _revert_snapshotted_user_ref_columns() -> None:
    op.execute(
        """DO $$
        DECLARE
            r RECORD;
            col text;
        BEGIN
            IF to_regclass('_phasec_saved_platform_fks') IS NULL THEN
                RETURN;
            END IF;
            FOR r IN
                SELECT * FROM _phasec_saved_platform_fks
                WHERE ref_schema = 'platform' AND ref_table = 'users'
            LOOP
                FOREACH col IN ARRAY r.src_columns LOOP
                    IF EXISTS (
                        SELECT 1 FROM information_schema.columns
                        WHERE table_schema = r.schema_name
                          AND table_name = r.table_name
                          AND column_name = col
                          AND data_type = 'uuid'
                    ) THEN
                        EXECUTE format(
                            'ALTER TABLE %I.%I ALTER COLUMN %I TYPE varchar(128)
                             USING %I::text',
                            r.schema_name, r.table_name, col, col);
                    END IF;
                END LOOP;
            END LOOP;
        END $$;"""
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
    #
    # Platform FKs come down first: they reference users.id, which the
    # legacy remap rewrites, and the remap must precede the tenant section
    # so a single-schema install has the old -> new mapping in
    # user_identities before its tenant columns are remapped.
    _save_and_drop_fks_platform()
    _remap_legacy_platform_user_ids()

    _save_and_drop_policies_current()
    _save_and_drop_fks_current()
    _remap_legacy_tenant_user_ids()
    for table, column in TENANT_COLUMNS:
        _alter_to_uuid_current(table, column)
    _restore_fks_current()
    _restore_policies_current()
    # The function only exists once patient_clinicians does; CREATE OR REPLACE
    # is a no-op-safe rewrite on every fan-out invocation.
    op.execute(_HAS_PATIENT_ACCESS_UUID_BODY)

    # Platform schema (idempotent across the per-tenant fan-out).
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
    # Any other column the FK snapshot saw referencing users(id) — e.g. a
    # deployment-defined table — must flip before its FK is restored.
    _convert_snapshotted_user_ref_columns()
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
    _revert_snapshotted_user_ref_columns()
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
