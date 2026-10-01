# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""The two reads that see the whole practice: who its clients are, and which
outside event is already booked.

A client belongs to the practice, not to one clinician, so matching a
calendar event or a feed code must see every chart in the practice — or it
makes a second chart for a colleague's client. Row-level security shows a
clinician only the charts they hold a grant on, which is right for every
other read and wrong for this one.

``practice_client_directory()`` is that exception, and it is narrow: for
each live chart in its own schema it returns the id, first and last name,
date of birth, email, and the ids of the clinicians holding a grant. Nothing
else — no notes, no other contact details, no diagnoses.

``practice_outside_appointment(source, calendar_id, event_id)`` is the
second, narrower still: the id of the live appointment following one
outside event, wherever in the practice it is. Two clinicians following one
shared calendar must never book the same session twice, and the second to
answer links to the first's appointment; the appointment table shows each
clinician only their own rows, so the link needs this.

**Why they need a role of their own.** The app's role owns the tenant tables
and they are ``FORCE ROW LEVEL SECURITY``, so a ``SECURITY DEFINER`` function
owned by that role runs under the very policies it is meant to see past.
Instead the functions are owned by ``pablo_practice_directory``: a role that
cannot log in, cannot bypass RLS, may read only the columns above, and is
admitted by one SELECT policy on each table it reads, naming it and no one
else. The app's role is a member that can hand it ownership but does not
inherit its privileges, so every ordinary read keeps exactly the policy it
had.

The template carries the functions, not their owner or grants (the capture
strips both), so :func:`apply_practice_directory_access` puts them on every
practice schema: at provisioning and on every migrate fan-out, through
``enable_rls_on_schema``, and in the revisions that add each function.

**Changing a function later.** Once a schema's function belongs to the
directory role, the role that runs migrations no longer owns it and cannot
``CREATE OR REPLACE`` or ``DROP`` it directly ("must be owner of function").
A revision that changes the body does it as the owner, inside
:func:`as_directory_owner`::

    with as_directory_owner(db, schema):
        db.execute(text(create_directory_function_sql(schema)))

That grants the role ``CREATE`` on the schema, ``SET ROLE``\\s to it, and on
the way out resets the role and revokes ``CREATE`` again, so the role keeps
nothing it did not have. :func:`drop_directory_function` is the same recipe
for a downgrade. A schema whose function is still owned by the migrator —
the template schema, or one the access pass has not reached — takes the
statement directly.
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import TYPE_CHECKING, NamedTuple

from sqlalchemy import text

if TYPE_CHECKING:
    from collections.abc import Iterator

    from sqlalchemy.engine import Connection
    from sqlalchemy.orm import Session

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
GRANT_COLUMNS = ("patient_id", "user_id", "expires_at")
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

_POLICY = "rls_practice_directory_read"
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


def create_directory_function_sql(schema: str) -> str:
    """The directory function, qualified to ``schema``.

    Every reference is schema-qualified and the search path is pinned, so
    nothing a caller puts on its own path can stand in for a table. It is
    created pinned to ``pg_catalog, pg_temp`` because that is what the
    template can carry: the capture rewrites ``practice.`` qualifiers for each
    new schema, and would not rewrite a schema name inside a ``SET
    search_path`` clause. :func:`apply_practice_directory_access` then pins
    each schema's own copy to ``pg_catalog, <schema>, pg_temp``.

    A session with no clinician armed gets nothing. That is the portal's
    patient principal and any unarmed job — the same principals the
    ``patients`` insert policy refuses.
    """
    return _DIRECTORY.sql.replace(_SCHEMA, schema)


def outside_appointment_function_sql(schema: str) -> str:
    """The outside-appointment lookup, qualified to ``schema``; see above."""
    return _OUTSIDE_APPOINTMENT.sql.replace(_SCHEMA, schema)


def directory_function_owner(
    db: Session | Connection, schema: str, function: str = DIRECTORY_FUNCTION
) -> str | None:
    """Who owns this schema's function, or ``None`` when it has none."""
    owner: str | None = db.execute(
        text("SELECT pg_get_userbyid(proowner) FROM pg_proc WHERE oid = to_regprocedure(:fn)"),
        {"fn": _definer(function).regprocedure(schema)},
    ).scalar()
    return owner


def create_directory_function(db: Session | Connection, schema: str) -> None:
    """Create this schema's directory function, unless the directory role already owns it.

    A schema built from the template already has it, owned by the role, and
    a re-walk of the chain over such a schema must not try to replace a
    function the migrator no longer owns. A later revision that changes the
    body replaces it inside :func:`as_directory_owner` instead.
    """
    _create(db, schema, _DIRECTORY)


def create_outside_appointment_function(db: Session | Connection, schema: str) -> None:
    """Create this schema's outside-appointment lookup; the same rules as the directory's."""
    _create(db, schema, _OUTSIDE_APPOINTMENT)


def _create(db: Session | Connection, schema: str, definer: _Definer) -> None:
    if directory_function_owner(db, schema, definer.name) == DIRECTORY_ROLE:
        return
    db.execute(text(definer.sql.replace(_SCHEMA, schema)))


def drop_directory_function(db: Session | Connection, schema: str) -> None:
    """Drop this schema's directory function, as its owner when that is the role."""
    _drop(db, schema, _DIRECTORY)


def drop_outside_appointment_function(db: Session | Connection, schema: str) -> None:
    """Drop this schema's outside-appointment lookup, as its owner when that is the role."""
    _drop(db, schema, _OUTSIDE_APPOINTMENT)


def _drop(db: Session | Connection, schema: str, definer: _Definer) -> None:
    statement = text(f"DROP FUNCTION IF EXISTS {definer.regprocedure(schema)}")
    if directory_function_owner(db, schema, definer.name) != DIRECTORY_ROLE:
        db.execute(statement)
        return
    with as_directory_owner(db, schema):
        db.execute(statement)


@contextmanager
def as_directory_owner(db: Session | Connection, schema: str) -> Iterator[None]:
    """Run statements as the directory role, for replacing or dropping its function.

    The role needs ``CREATE`` on the schema to replace a function there; it
    has it only while this block runs.
    """
    _require_role(db)
    db.execute(text(f"GRANT CREATE ON SCHEMA {schema} TO {DIRECTORY_ROLE}"))
    # LOCAL: the role ends with the transaction, whatever the caller does next.
    db.execute(text(f"SET LOCAL ROLE {DIRECTORY_ROLE}"))
    try:
        yield
    finally:
        db.execute(text("RESET ROLE"))
        db.execute(text(f"REVOKE CREATE ON SCHEMA {schema} FROM {DIRECTORY_ROLE}"))


def apply_practice_directory_access(db: Session | Connection, schema: str) -> None:
    """Give one practice schema's directory functions their owner, grants and policies.

    Idempotent. Each function is handled only once its schema's chain has
    reached the revision that adds it; that revision calls this itself.
    ``schema`` must already be validated by the caller.
    """
    present = [
        (definer, owner)
        for definer in _DEFINERS
        if (owner := directory_function_owner(db, schema, definer.name)) is not None
    ]
    if not present:
        return
    _require_role(db)

    db.execute(text(f"GRANT USAGE ON SCHEMA {schema} TO {DIRECTORY_ROLE}"))
    for definer, owner in present:
        for table, columns in definer.reads:
            db.execute(
                text(f"GRANT SELECT ({', '.join(columns)}) ON {schema}.{table} TO {DIRECTORY_ROLE}")
            )
            # Permissive policies OR together, so these widen reads for the
            # directory role only. Every other role keeps exactly its own.
            db.execute(text(f"DROP POLICY IF EXISTS {_POLICY} ON {schema}.{table}"))
            db.execute(
                text(
                    f"CREATE POLICY {_POLICY} ON {schema}.{table} "
                    f"FOR SELECT TO {DIRECTORY_ROLE} USING ({_ARMED})"
                )
            )

        _pin_search_path(db, schema, owner, definer)

        if owner != DIRECTORY_ROLE:
            # A new owner must be able to create in the schema; it needs that
            # only for this one statement, so it does not keep it.
            db.execute(text(f"GRANT CREATE ON SCHEMA {schema} TO {DIRECTORY_ROLE}"))
            db.execute(
                text(f"ALTER FUNCTION {definer.regprocedure(schema)} OWNER TO {DIRECTORY_ROLE}")
            )
            db.execute(text(f"REVOKE CREATE ON SCHEMA {schema} FROM {DIRECTORY_ROLE}"))


def _pin_search_path(db: Session | Connection, schema: str, owner: str, definer: _Definer) -> None:
    """Pin the function's search path to ``pg_catalog, <schema>, pg_temp``.

    The body is fully qualified, but the row policies it runs under are not
    all its own: ``patients`` also carries ``has_patient_access``, whose body
    names ``patient_clinicians`` unqualified. Inside the function that has to
    resolve to this practice's table, or planning the read fails. The
    template can only carry ``pg_catalog, pg_temp`` (its capture does not
    rewrite a schema name in a ``SET`` clause), so each schema gets its own
    path here. ``pg_catalog`` stays first, so nothing in the schema can stand
    in for a built-in.
    """
    wanted = f"search_path=pg_catalog, {schema}, pg_temp"
    current = db.execute(
        text("SELECT proconfig FROM pg_proc WHERE oid = to_regprocedure(:fn)"),
        {"fn": definer.regprocedure(schema)},
    ).scalar()
    if current == [wanted]:
        return
    statement = text(
        f"ALTER FUNCTION {definer.regprocedure(schema)} "
        f"SET search_path = pg_catalog, {schema}, pg_temp"
    )
    if owner != DIRECTORY_ROLE:
        db.execute(statement)
        return
    with as_directory_owner(db, schema):
        db.execute(statement)


def _definer(function: str) -> _Definer:
    return next(d for d in _DEFINERS if d.name == function)


def _require_role(db: Session | Connection) -> None:
    """Fail with the fix, rather than with Postgres' "role does not exist"."""
    ready = db.execute(
        text("SELECT pg_has_role(current_user, :role, 'SET') FROM pg_roles WHERE rolname = :role"),
        {"role": DIRECTORY_ROLE},
    ).scalar()
    if not ready:
        msg = (
            f"The database role {DIRECTORY_ROLE} is missing, or the role Pablo connects "
            f"as cannot SET ROLE to it. Run the platform migrations, or create it by "
            f"hand: see {DIRECTORY_ROLE_DOC}."
        )
        raise RuntimeError(msg)


__all__ = [
    "APPOINTMENT_COLUMNS",
    "DIRECTORY_FUNCTION",
    "DIRECTORY_ROLE",
    "GRANT_COLUMNS",
    "OUTSIDE_APPOINTMENT_FUNCTION",
    "PATIENT_COLUMNS",
    "apply_practice_directory_access",
    "as_directory_owner",
    "create_directory_function",
    "create_directory_function_sql",
    "create_outside_appointment_function",
    "directory_function_owner",
    "drop_directory_function",
    "drop_outside_appointment_function",
    "outside_appointment_function_sql",
]
