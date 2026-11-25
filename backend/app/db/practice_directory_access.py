# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import text

from .practice_directory_definitions import (
    _ARMED,
    _DEFINERS,
    _POLICY,
    DIRECTORY_ROLE,
    _Definer,
)

if TYPE_CHECKING:
    from sqlalchemy.engine import Connection
    from sqlalchemy.orm import Session

from .practice_directory_functions import (
    as_directory_owner,
    directory_function_owner,
    require_directory_role,
)


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
    require_directory_role(db)

    db.execute(text(f"GRANT USAGE ON SCHEMA {schema} TO {DIRECTORY_ROLE}"))
    for definer, owner in present:
        for table, columns in definer.reads:
            granted = _existing_columns(db, schema, table, columns)
            db.execute(
                text(f"GRANT SELECT ({', '.join(granted)}) ON {schema}.{table} TO {DIRECTORY_ROLE}")
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


def directory_access_sql(schema: str) -> str:
    """What gives a schema at head its directory access, apart from the policies.

    The schema usage, the column grants, each function's pinned search path and
    its owner: the same end state :func:`apply_practice_directory_access`
    leaves, written out as SQL. The tenant template carries this with the
    schema placeholder for ``schema``, so a new practice gets it when the
    template is applied. The three read policies are not here: the template
    captures those as it captures every other policy.

    Statement order matters. The path is pinned while the function still
    belongs to whoever created it, and the owner changes last; the role holds
    ``CREATE`` on the schema only for that one statement.
    """
    statements = [f"GRANT USAGE ON SCHEMA {schema} TO {DIRECTORY_ROLE};"]
    for definer in _DEFINERS:
        statements.extend(
            f"GRANT SELECT ({', '.join(columns)}) ON {schema}.{table} TO {DIRECTORY_ROLE};"
            for table, columns in definer.reads
        )
    for definer in _DEFINERS:
        function = definer.regprocedure(schema)
        statements.extend(
            [
                f"ALTER FUNCTION {function} SET search_path = pg_catalog, {schema}, pg_temp;",
                f"GRANT CREATE ON SCHEMA {schema} TO {DIRECTORY_ROLE};",
                f"ALTER FUNCTION {function} OWNER TO {DIRECTORY_ROLE};",
                f"REVOKE CREATE ON SCHEMA {schema} FROM {DIRECTORY_ROLE};",
            ]
        )
    return "\n\n".join(statements) + "\n"


def _existing_columns(
    db: Session | Connection, schema: str, table: str, columns: tuple[str, ...]
) -> list[str]:
    """Those of *columns* the table has yet.

    This pass runs mid-chain, from the revision that adds each function, and
    a column the role reads may come from a later revision — ``starts_at``,
    which ``has_patient_access`` reads under the ``patients`` policy, is one.
    The pass at head grants it.
    """
    present = set(
        db.execute(
            text(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema = :schema AND table_name = :table"
            ),
            {"schema": schema, "table": table},
        ).scalars()
    )
    return [column for column in columns if column in present]


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
