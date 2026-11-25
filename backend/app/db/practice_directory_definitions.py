# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""SQL definitions and column permissions for practice-wide lookups."""

from typing import NamedTuple

#: The role that owns the directory functions. Created by the platform chain.
DIRECTORY_ROLE = "pablo_practice_directory"
DIRECTORY_FUNCTION = "practice_client_directory"
OUTSIDE_APPOINTMENT_FUNCTION = "practice_outside_appointment"
#: Where an operator finds how to create the role by hand.
DIRECTORY_ROLE_DOC = "docs/SELF_HOSTING_HIPAA_GUIDE.md#database-roles"

#: Everything the directory reads, and so everything the role may read there.
PATIENT_COLUMNS = (
    "id",
    "first_name",
    "last_name",
    "date_of_birth",
    "email",
    "status",
    "deleted_at",
)
#: ``starts_at`` is not read by the directory itself: ``has_patient_access``
#: reads it, under the ``patients`` policy the directory's read also meets.
GRANT_COLUMNS = ("patient_id", "user_id", "starts_at", "expires_at")
#: Everything the outside-appointment lookup reads: ids and a status, no
#: time, no patient.
APPOINTMENT_COLUMNS = (
    "id",
    "outside_source",
    "outside_calendar_id",
    "outside_event_id",
    "status",
    "created_at",
)

#: The read policy on each table a directory function reads, for the role alone.
DIRECTORY_POLICY = "rls_practice_directory_read"
_POLICY = DIRECTORY_POLICY
#: The same principal check the functions make, on the policies too: a
#: ``SET ROLE`` from a connection with no clinician armed reads nothing.
_ARMED = "coalesce(current_setting('app.current_user_id', true), '') <> ''"

# Substituted per schema, as the tenant template substitutes its own.
_SCHEMA = "__SCHEMA__"
_DIRECTORY_SQL = """
    CREATE OR REPLACE FUNCTION __SCHEMA__.practice_client_directory()
    RETURNS TABLE (
        id uuid,
        first_name character varying,
        last_name character varying,
        date_of_birth date,
        email character varying,
        clinician_ids uuid[]
    )
    LANGUAGE sql STABLE SECURITY DEFINER
    SET search_path = pg_catalog, pg_temp
    AS $$
        SELECT p.id, p.first_name, p.last_name, p.date_of_birth, p.email,
               coalesce(
                   array_agg(g.user_id ORDER BY g.user_id)
                       FILTER (WHERE g.user_id IS NOT NULL),
                   '{}'::uuid[]
               )
        FROM __SCHEMA__.patients p
        LEFT JOIN __SCHEMA__.patient_clinicians g
          ON g.patient_id = p.id
         AND (g.expires_at IS NULL OR g.expires_at > now())
        WHERE p.deleted_at IS NULL
          AND p.status <> 'pending'
          AND coalesce(current_setting('app.current_user_id', true), '') <> ''
        GROUP BY p.id
    $$
"""
_OUTSIDE_APPOINTMENT_SQL = """
    CREATE OR REPLACE FUNCTION __SCHEMA__.practice_outside_appointment(
        p_source text, p_calendar_id text, p_event_id text
    )
    RETURNS uuid
    LANGUAGE sql STABLE SECURITY DEFINER
    SET search_path = pg_catalog, pg_temp
    AS $$
        SELECT a.id
        FROM __SCHEMA__.appointments a
        WHERE a.outside_source = p_source
          AND a.outside_calendar_id = p_calendar_id
          AND a.outside_event_id = p_event_id
          AND a.status <> 'cancelled'
          AND coalesce(current_setting('app.current_user_id', true), '') <> ''
        ORDER BY a.created_at, a.id
        LIMIT 1
    $$
"""


class _Definer(NamedTuple):
    """One function the directory role owns, and what it reads."""

    name: str
    #: The argument types, as ``to_regprocedure`` wants them.
    signature: str
    sql: str
    reads: tuple[tuple[str, tuple[str, ...]], ...]

    def regprocedure(self, schema: str) -> str:
        return f"{schema}.{self.name}{self.signature}"


_DIRECTORY = _Definer(
    DIRECTORY_FUNCTION,
    "()",
    _DIRECTORY_SQL,
    (("patients", PATIENT_COLUMNS), ("patient_clinicians", GRANT_COLUMNS)),
)
_OUTSIDE_APPOINTMENT = _Definer(
    OUTSIDE_APPOINTMENT_FUNCTION,
    "(text, text, text)",
    _OUTSIDE_APPOINTMENT_SQL,
    (("appointments", APPOINTMENT_COLUMNS),),
)
_DEFINERS = (_DIRECTORY, _OUTSIDE_APPOINTMENT)
