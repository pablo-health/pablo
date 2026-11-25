# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

from __future__ import annotations

from contextlib import contextmanager
from typing import TYPE_CHECKING

from sqlalchemy import text

from .practice_directory_definitions import (
    _DEFINERS,
    _DIRECTORY,
    _OUTSIDE_APPOINTMENT,
    _SCHEMA,
    DIRECTORY_FUNCTION,
    DIRECTORY_ROLE,
    DIRECTORY_ROLE_DOC,
    _Definer,
)

if TYPE_CHECKING:
    from collections.abc import Iterator

    from sqlalchemy.engine import Connection
    from sqlalchemy.orm import Session


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


def require_directory_role(db: Session | Connection) -> None:
    """Fail with the fix if the directory role is missing or cannot be assumed.

    Provisioning calls this before it applies the template's row-level
    security section, which names the role: Postgres' own "role does not
    exist" would name neither the cause nor the remedy.
    """
    _require_role(db)


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
