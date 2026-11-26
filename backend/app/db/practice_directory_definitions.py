# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""SQL definitions and column permissions for practice-wide lookups."""

from typing import NamedTuple

#: The role that owns the directory functions. Created by the platform chain.
DIRECTORY_ROLE = "pablo_practice_directory"
DIRECTORY_FUNCTION = "practice_client_directory"
OUTSIDE_APPOINTMENT_FUNCTION = "practice_outside_appointment"
CLAIM_SOURCE_FUNCTION = "practice_claim_source"
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
#: Everything the claim source reads: what claim assembly snapshots from the
#: visit, the client, the active coverage, the visit's type and the clinician
#: who held it. No note, transcript, intake answer or contact beyond the
#: claim's own fields.
CLAIM_APPOINTMENT_COLUMNS = (
    "id",
    "user_id",
    "patient_id",
    "appointment_type_id",
    "start_at",
    "service_code",
    "unit_count",
    "modifiers",
    "diagnosis_codes",
    "place_of_service",
    "video_link",
)
CLAIM_PATIENT_COLUMNS = (
    "id",
    "first_name",
    "last_name",
    "date_of_birth",
    "sex",
    "address_line1",
    "address_line2",
    "city",
    "state",
    "postal_code",
    "phone",
    "rate_cents",
    "deleted_at",
)
CLAIM_COVERAGE_COLUMNS = (
    "id",
    "patient_id",
    "payer_id",
    "member_id",
    "group_number",
    "plan_name",
    "subscriber_relationship",
    "subscriber_first_name",
    "subscriber_last_name",
    "subscriber_date_of_birth",
    "subscriber_sex",
    "subscriber_address_line1",
    "subscriber_address_line2",
    "subscriber_city",
    "subscriber_state",
    "subscriber_postal_code",
    "active",
    "created_at",
)
CLAIM_APPOINTMENT_TYPE_COLUMNS = ("id", "user_id", "default_fee_cents")
CLAIM_CLINICIAN_COLUMNS = ("user_id", "npi_number", "taxonomy_code")

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

# One visit, as a claim names it. The coverage is the client's active one,
# newest first, as the coverage repository picks it; the type and the
# profile are those of the clinician who held the session.
_CLAIM_SOURCE_SQL = """
    CREATE OR REPLACE FUNCTION __SCHEMA__.practice_claim_source(p_appointment_id uuid)
    RETURNS TABLE (
        appointment_id uuid,
        clinician_id uuid,
        patient_id uuid,
        appointment_type_id uuid,
        start_at timestamp with time zone,
        service_code character varying,
        unit_count integer,
        modifiers jsonb,
        diagnosis_codes jsonb,
        place_of_service character varying,
        video_link text,
        first_name character varying,
        last_name character varying,
        date_of_birth date,
        sex character varying,
        address_line1 character varying,
        address_line2 character varying,
        city character varying,
        state character varying,
        postal_code character varying,
        phone character varying,
        rate_cents integer,
        coverage_id uuid,
        payer_id uuid,
        member_id character varying,
        group_number character varying,
        plan_name character varying,
        subscriber_relationship character varying,
        subscriber_first_name character varying,
        subscriber_last_name character varying,
        subscriber_date_of_birth date,
        subscriber_sex character varying,
        subscriber_address_line1 character varying,
        subscriber_address_line2 character varying,
        subscriber_city character varying,
        subscriber_state character varying,
        subscriber_postal_code character varying,
        default_fee_cents integer,
        npi_number character varying,
        taxonomy_code character varying
    )
    LANGUAGE sql STABLE SECURITY DEFINER
    SET search_path = pg_catalog, pg_temp
    AS $$
        SELECT a.id, a.user_id, a.patient_id, a.appointment_type_id, a.start_at,
               a.service_code, a.unit_count, a.modifiers, a.diagnosis_codes,
               a.place_of_service, a.video_link,
               p.first_name, p.last_name, p.date_of_birth, p.sex, p.address_line1,
               p.address_line2, p.city, p.state, p.postal_code, p.phone, p.rate_cents,
               c.id, c.payer_id, c.member_id, c.group_number, c.plan_name,
               c.subscriber_relationship, c.subscriber_first_name,
               c.subscriber_last_name, c.subscriber_date_of_birth, c.subscriber_sex,
               c.subscriber_address_line1, c.subscriber_address_line2,
               c.subscriber_city, c.subscriber_state, c.subscriber_postal_code,
               t.default_fee_cents, cp.npi_number, cp.taxonomy_code
        FROM __SCHEMA__.appointments a
        JOIN __SCHEMA__.patients p
          ON p.id = a.patient_id AND p.deleted_at IS NULL
        LEFT JOIN LATERAL (
            SELECT pc.id, pc.payer_id, pc.member_id, pc.group_number, pc.plan_name,
                   pc.subscriber_relationship, pc.subscriber_first_name,
                   pc.subscriber_last_name, pc.subscriber_date_of_birth,
                   pc.subscriber_sex, pc.subscriber_address_line1,
                   pc.subscriber_address_line2, pc.subscriber_city,
                   pc.subscriber_state, pc.subscriber_postal_code
            FROM __SCHEMA__.patient_coverage pc
            WHERE pc.patient_id = a.patient_id AND pc.active
            ORDER BY pc.created_at DESC, pc.id DESC
            LIMIT 1
        ) c ON true
        LEFT JOIN __SCHEMA__.appointment_types t
          ON t.id = a.appointment_type_id AND t.user_id = a.user_id
        LEFT JOIN __SCHEMA__.clinician_profiles cp
          ON cp.user_id = a.user_id
        WHERE a.id = p_appointment_id
          AND coalesce(current_setting('app.current_user_id', true), '') <> ''
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
_CLAIM_SOURCE = _Definer(
    CLAIM_SOURCE_FUNCTION,
    "(uuid)",
    _CLAIM_SOURCE_SQL,
    (
        ("appointments", CLAIM_APPOINTMENT_COLUMNS),
        ("patients", CLAIM_PATIENT_COLUMNS),
        ("patient_coverage", CLAIM_COVERAGE_COLUMNS),
        ("appointment_types", CLAIM_APPOINTMENT_TYPE_COLUMNS),
        ("clinician_profiles", CLAIM_CLINICIAN_COLUMNS),
    ),
)
_DEFINERS = (_DIRECTORY, _OUTSIDE_APPOINTMENT, _CLAIM_SOURCE)
