# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Make a practice schema's row-level security match the committed SQL.

``tenant_template.sql`` says what every tenant table's row-level security is:
which tables have it enabled and forced, and each policy, word for word. This
module reads that out of the file and makes a live practice schema agree with
it. It creates what the file has and the schema lacks, drops what the schema
has and the file no longer does, and leaves a table that already agrees alone.

It knows no table names and no policy shapes. What a table's policy should be
is decided when the template is generated, and the file is the record of it.

**Which tables it touches.** Only the ones a file creates. A practice schema
can hold tables that came from somewhere else; their isolation is not this
module's to decide, so they are counted, named in one log line and left exactly
as they are.

**Which policies it drops.** On a table it does touch, every live policy the
file lacks, whatever its name and whoever created it. There are no exceptions
to name: a policy that should exist is in the file.

**How a difference is found.** See ``rls_plan``, which does the comparing. In
short: a live policy is compared with the file's statement as ``pg_dump`` text,
and a recorded hash covers a policy Postgres stores in another form.

**What "altered" counts.** Tables whose flags or policies ended up different.
A policy dropped and created again into exactly what it was, to record its
hash, is not a change and is not counted.

Nothing here commits. The caller owns the transaction.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from .rls_desired import DesiredTable, desired_from_sql
from .rls_plan import (
    APPLIED_PREFIX,
    applied_fingerprint,
    plan_schema,
    quote_identifier,
    render_policy,
)
from .rls_state import LivePolicy, LiveRls, read_live_rls

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping

    from sqlalchemy.orm import Session

    from . import RlsReconcileCounts

__all__ = [
    "APPLIED_PREFIX",
    "DesiredTable",
    "applied_fingerprint",
    "apply_tenant_rls",
    "desired_from_sql",
    "render_policy",
    "tenant_rls_differences",
]

logger = logging.getLogger(__name__)


def tenant_rls_differences(
    session: Session, schema_name: str, desired: Mapping[str, DesiredTable]
) -> dict[str, list[str]]:
    """What :func:`apply_tenant_rls` would run on this schema, by table. Reads only.

    Empty means the schema already says what the SQL says. A table whose only
    difference is how Postgres stored a statement appears here too, with the
    drop and create that would record it; ``apply_tenant_rls`` does not count
    that as a change, and this cannot tell without running it.
    """
    from . import _validate_schema_name

    _validate_schema_name(schema_name)
    return plan_schema(session, schema_name, desired).statements


def apply_tenant_rls(
    session: Session, schema_name: str, desired: Mapping[str, DesiredTable]
) -> RlsReconcileCounts:
    """Make ``schema_name``'s row-level security equal ``desired``.

    ``desired`` comes from :func:`desired_from_sql`; parse once and pass it to
    every schema. Returns how many tables were checked, how many were passed
    over because no SQL creates them, and how many ended up different from how
    they started. Does not commit: each table it changes stays locked until
    the caller does, so commit once per schema.

    Do not run this and ``enable_rls_on_schema`` over the same schema. Each
    reads the other's comment on a policy as not its own and rebuilds it.
    """
    # Imported here: the package imports this module's callers.
    from . import RlsReconcileCounts, _validate_schema_name

    _validate_schema_name(schema_name)
    plan = plan_schema(session, schema_name, desired)

    if plan.missing:
        logger.warning(
            "Row-level security: %d table(s) in the SQL and not in %s, nothing to apply: %s",
            len(plan.missing),
            schema_name,
            ", ".join(plan.missing),
        )
    if plan.unknown:
        logger.warning(
            "Row-level security: %d table(s) in %s that no SQL file creates, left as they are: %s",
            len(plan.unknown),
            schema_name,
            ", ".join(plan.unknown),
        )

    altered = 0
    if plan.statements:
        _execute(session, [s for statements in plan.statements.values() for s in statements])
        after = read_live_rls(session, schema_name)
        _execute(session, _record_stored_forms(schema_name, desired, plan.created, after))
        altered = sum(
            1 for name in plan.statements if _state(plan.before, name) != _state(after, name)
        )

    logger.info(
        "Row-level security on %s: %d table(s) checked, %d altered, %d left alone",
        schema_name,
        plan.checked,
        altered,
        len(plan.unknown),
    )
    return RlsReconcileCounts(applied=plan.checked, skipped=len(plan.unknown), altered=altered)


def _record_stored_forms(
    schema_name: str,
    desired: Mapping[str, DesiredTable],
    created: Mapping[str, list[str]],
    after: LiveRls,
) -> list[str]:
    """Comments for the policies just created that read back unlike their statement."""
    comments: list[str] = []
    for table_name, names in created.items():
        qualified = f"{quote_identifier(schema_name)}.{quote_identifier(table_name)}"
        for name in names:
            wanted = desired[table_name].policies[name]
            stored = render_policy(schema_name, table_name, name, after.policies[table_name][name])
            if stored != wanted:
                value = applied_fingerprint(wanted, stored)
                comments.append(
                    f"COMMENT ON POLICY {quote_identifier(name)} ON {qualified} IS '{value}'"
                )
    return comments


def _state(
    live: LiveRls, table_name: str
) -> tuple[tuple[bool, bool] | None, dict[str, LivePolicy]]:
    """A table's flags and policies, comments aside: a comment is bookkeeping."""
    policies = live.policies.get(table_name, {})
    return live.flags.get(table_name), {
        name: policy._replace(comment=None) for name, policy in policies.items()
    }


def _execute(session: Session, statements: Iterable[str]) -> None:
    """Run DDL on the session's own connection, as a restore would run it.

    **No parameter handling.** A policy body can hold a ``%``
    (``LIKE 'calendar:%'``) and a ``:name`` inside a string literal.
    ``text()`` would read the second as a bind parameter, and any execute that
    hands the driver a parameter collection makes psycopg2 read the first as a
    placeholder. A bare cursor does neither; ``provisioning.apply_template_sql``
    applies the template the same way for the same reason.

    **An empty search path.** The file's statements came out of ``pg_dump``,
    which qualifies every name outside ``pg_catalog``. Running them with
    nothing else on the path means a name can only resolve to what the file
    says, whatever the caller's session had on its own path. The caller's path
    is put back before returning.

    If a statement fails, the path is not put back here, and must not be: the
    transaction is aborted, so one more statement would raise its own error
    and hide the one that matters. The empty path was set for this transaction
    only, and goes when the caller rolls it back.
    """
    statements = list(statements)
    if not statements:
        return
    cursor = session.connection().connection.cursor()
    try:
        cursor.execute("SELECT pg_catalog.current_setting('search_path')")
        row = cursor.fetchone()
        previous = row[0] if row else ""
        cursor.execute("SELECT pg_catalog.set_config('search_path', '', true)")
        for statement in statements:
            cursor.execute(statement)
        cursor.execute("SELECT pg_catalog.set_config('search_path', %s, true)", (previous,))
    finally:
        cursor.close()
