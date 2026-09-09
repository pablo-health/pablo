# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Move a pre-provisioning deployment onto a real practice schema (PABLO-2g6.1's parent).

Boot used to register the live practice against ``practice`` — the schema that
is also the provisioning template, and the one ``enable_rls_on_schema`` returns
early on. So the schema holding real charts ran with no row policies at all.
Boot now provisions ``practice_default`` and registers that instead, but a
deployment that already exists is deliberately left alone and only warned about,
because moving live charts is a migration with a pre-flight, not a line in a
startup path. This module is that migration.

**Why a pre-flight, and why it refuses rather than reports.** The end state
turns FORCE ROW LEVEL SECURITY on over records that have been living without
it. Under FORCE RLS a row that satisfies no policy does not raise — it becomes
*invisible*. A chart that is silently not there reads as "the patient was
deleted" or "search is broken", and nobody connects it to a migration that
reported success weeks earlier. Migration ``777b846ab944`` backfilled
``patient_clinicians`` from ``patients.user_id``, so a grant SHOULD exist for
every row; "should" is the wrong confidence for an operation whose failure mode
is disappearing records.

So the pre-flight counts, per table, the rows that would be visible to NOBODY
once policies apply, and the migration refuses to run unless every count is
zero.

**Staying honest about what the policies are.** The predicates below mirror the
branches in ``enable_rls_on_schema``. A copy can drift from its original, and a
drifted copy here is worse than none: it would report zero invisible rows for a
shape it no longer understands. Two things guard that. ``_classify`` raises on
any table it does not recognise, exactly as ``enable_rls_on_schema`` raises
rather than leave a table deny-all. And ``test_single_practice_migration.py``
applies the REAL policies to a provisioned schema, reads ``pg_policies`` back,
and asserts the classification here matches the policy actually created — so a
new branch there fails a test here rather than silently under-reporting.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING

from sqlalchemy import text

from . import (
    DEFAULT_PRACTICE_ID,
    DEFAULT_PRACTICE_OWN_SCHEMA,
    DEFAULT_PRACTICE_SCHEMA,
    PLATFORM_SCHEMA,
    _validate_schema_name,
    enable_rls_on_schema,
    not_row_scoped_tenant_tables,
)

if TYPE_CHECKING:
    from sqlalchemy.engine import Connection, Engine

logger = logging.getLogger(__name__)

#: Categories whose ``patient_documents`` rows collapse to uploader-only access
#: (mirrors the ``rls_patient_doc_access`` policy).
_RESTRICTED_DOC_CATEGORIES = ("therapist_private", "psychotherapy_notes")

#: A patient is reachable if ANY unexpired grant names them. ``has_patient_access``
#: also matches on user_id; the question here is "visible to anybody at all", so
#: the user is left out and only the liveness test is kept — including
#: ``expires_at``, because the policy checks it and an expired grant hides the row.
_LIVE_GRANT = (
    "EXISTS (SELECT 1 FROM {schema}.patient_clinicians pc "
    "WHERE pc.patient_id = {ref} AND (pc.expires_at IS NULL OR pc.expires_at > now()))"
)


class Shape(Enum):
    """Which ``enable_rls_on_schema`` branch a table takes."""

    USER_ID = "user_id"
    #: Same orphan predicate as USER_ID — reads are gated on ``user_id`` — but a
    #: policy set of its own (``rls_audit_actor_access`` plus the purge arms), so
    #: it is a distinct shape rather than a USER_ID table that happens to differ.
    AUDIT_LOGS = "audit_actor"
    UPLOADER = "uploaded_by_user_id"
    PATIENT_ACCESS_BY_ID = "has_patient_access(id)"
    PATIENT_ACCESS_BY_PATIENT_ID = "has_patient_access(patient_id)"
    PATIENT_DOCUMENTS = "patient_doc_access"
    CHAT_MESSAGES = "chat_message_access"


@dataclass(frozen=True)
class OrphanCount:
    """Rows in one table that no principal could read once policies apply."""

    table: str
    shape: Shape
    orphaned: int
    total: int


class PreflightError(RuntimeError):
    """The pre-flight found rows that would become invisible. Nothing was changed."""


#: Ordered exactly as ``enable_rls_on_schema``'s if/elif chain. Order is the
#: whole content: ``audit_logs`` and ``patient_clinicians`` both carry
#: ``user_id`` and would be swallowed by the generic rule if they came after it.
_CLASSIFY_RULES: list[tuple[str, Shape]] = [
    ("patient_documents", Shape.PATIENT_DOCUMENTS),
    ("patient_clinicians", Shape.USER_ID),
    ("patients", Shape.PATIENT_ACCESS_BY_ID),
    ("audit_logs", Shape.AUDIT_LOGS),
]

#: Applied after the by-name rules, in this order.
_CLASSIFY_BY_COLUMN: list[tuple[str, Shape]] = [
    ("user_id", Shape.USER_ID),
    ("patient_id", Shape.PATIENT_ACCESS_BY_PATIENT_ID),
]

#: Reached only when neither of the scoping columns is present.
_CLASSIFY_FALLBACK_RULES: list[tuple[str, Shape]] = [
    ("chat_messages", Shape.CHAT_MESSAGES),
    ("compliance_documents", Shape.UPLOADER),
]


def _classify(table_name: str, columns: set[str]) -> Shape:
    """Which policy shape ``enable_rls_on_schema`` would give this table."""
    for name, shape in _CLASSIFY_RULES:
        if table_name == name:
            return shape
    for column, shape in _CLASSIFY_BY_COLUMN:
        if column in columns:
            return shape
    for name, shape in _CLASSIFY_FALLBACK_RULES:
        if table_name == name:
            return shape
    raise RuntimeError(
        f"single-practice pre-flight: no known RLS policy shape for '{table_name}' "
        f"(columns present: {sorted(columns)}). enable_rls_on_schema would raise on "
        f"this table too — add the branch in both places, or register the table as "
        f"not-row-scoped. Refusing to report it as safe."
    )


def _user_owned(_schema: str, _table: str) -> str:
    """Gated on ``user_id``: readable by nobody exactly when it names nobody.

    ``audit_logs``' actor split changes WHICH GUC is compared, not whether an
    owner exists, so it shares this predicate.
    """
    return "user_id IS NULL"


def _uploader_owned(_schema: str, _table: str) -> str:
    return "uploaded_by_user_id IS NULL"


def _patient_by_id(schema: str, table: str) -> str:
    return "NOT " + _LIVE_GRANT.format(schema=schema, ref=f"{schema}.{table}.id")


def _patient_by_patient_id(schema: str, table: str) -> str:
    return "NOT " + _LIVE_GRANT.format(schema=schema, ref=f"{schema}.{table}.patient_id")


def _patient_document(schema: str, table: str) -> str:
    """Mirrors ``rls_patient_doc_access``: chart rows follow the grant, restricted
    categories collapse to the uploader."""
    restricted = ", ".join(f"'{c}'" for c in _RESTRICTED_DOC_CATEGORIES)
    chart_unreachable = "NOT " + _LIVE_GRANT.format(
        schema=schema, ref=f"{schema}.{table}.patient_id"
    )
    return (
        f"(category NOT IN ({restricted}) AND {chart_unreachable}) "
        f"OR (category IN ({restricted}) AND user_id IS NULL)"
    )


def _chat_message(schema: str, table: str) -> str:
    """Mirrors ``rls_chat_message_access``: reachable only through the parent
    conversation's patient. A message whose conversation is missing is
    unreachable too, which the EXISTS covers."""
    return (
        f"NOT EXISTS (SELECT 1 FROM {schema}.chat_conversations c "  # noqa: S608 — schema/table are validated identifiers, never user input
        f"WHERE c.id = {schema}.{table}.conversation_id AND "
        + _LIVE_GRANT.format(schema=schema, ref="c.patient_id")
        + ")"
    )


#: One entry per shape. A dispatch table rather than a branch chain so that
#: adding a shape without its predicate is a KeyError at the point of use —
#: loud — instead of a fall-through that reports the table clean.
_ORPHAN_PREDICATE = {
    Shape.USER_ID: _user_owned,
    Shape.AUDIT_LOGS: _user_owned,
    Shape.UPLOADER: _uploader_owned,
    Shape.PATIENT_ACCESS_BY_ID: _patient_by_id,
    Shape.PATIENT_ACCESS_BY_PATIENT_ID: _patient_by_patient_id,
    Shape.PATIENT_DOCUMENTS: _patient_document,
    Shape.CHAT_MESSAGES: _chat_message,
}


def _orphan_predicate(schema: str, table: str, shape: Shape) -> str:
    """SQL that is true for a row no principal could read."""
    return _ORPHAN_PREDICATE[shape](schema, table)


def _schema_exists(conn: Connection, schema: str) -> bool:
    return bool(
        conn.execute(
            text("SELECT 1 FROM information_schema.schemata WHERE schema_name = :s"),
            {"s": schema},
        ).first()
    )


def _tables_with_columns(conn: Connection, schema: str) -> dict[str, set[str]]:
    rows = conn.execute(
        text(
            "SELECT table_name, column_name FROM information_schema.columns "
            "WHERE table_schema = :s AND table_name != 'alembic_version'"
        ),
        {"s": schema},
    ).fetchall()
    out: dict[str, set[str]] = {}
    for table_name, column_name in rows:
        out.setdefault(table_name, set()).add(column_name)
    return out


def preflight(engine: Engine, schema: str = DEFAULT_PRACTICE_SCHEMA) -> list[OrphanCount]:
    """Count, per table, the rows that would become invisible under RLS.

    Reads only. Returns a row per RLS-forced table, including the clean ones, so
    an operator sees the whole picture rather than only the bad news — "0 of
    412" is the reassurance that makes the number worth printing.
    """
    _validate_schema_name(schema)
    not_row_scoped = not_row_scoped_tenant_tables()
    scoping_columns = {"user_id", "patient_id", "id"}

    results: list[OrphanCount] = []
    with engine.connect() as conn:
        if not _schema_exists(conn, schema):
            return results

        for table, columns in sorted(_tables_with_columns(conn, schema).items()):
            if table in not_row_scoped or not (columns & scoping_columns):
                continue
            shape = _classify(table, columns)
            predicate = _orphan_predicate(schema, table, shape)
            total, orphaned = conn.execute(
                text(
                    f"SELECT count(*), count(*) FILTER (WHERE {predicate}) "  # noqa: S608 — schema/table are validated identifiers, never user input
                    f"FROM {schema}.{table}"
                )
            ).one()
            results.append(OrphanCount(table, shape, int(orphaned), int(total)))
    return results


def format_report(counts: list[OrphanCount]) -> str:
    """A human-readable pre-flight report, worst first."""
    if not counts:
        return "No RLS-forced tables found — nothing to check."
    width = max(len(c.table) for c in counts)
    lines = [f"{'table'.ljust(width)}  invisible / total  policy"]
    for c in sorted(counts, key=lambda c: (-c.orphaned, c.table)):
        flag = "  <-- WOULD BE LOST" if c.orphaned else ""
        lines.append(
            f"{c.table.ljust(width)}  {c.orphaned:>9} / {c.total:<5}  {c.shape.value}{flag}"
        )
    return "\n".join(lines)


def is_migrated(engine: Engine) -> bool:
    """Whether this deployment already lives in its own practice schema.

    True also for a deployment that was never in the old shape — a fresh install
    boots straight onto ``practice_default``, so "nothing to migrate" and
    "already migrated" are the same answer to the only question callers ask.
    """
    with engine.connect() as conn:
        row = conn.execute(
            text(
                f"SELECT schema_name FROM {PLATFORM_SCHEMA}.practices WHERE id = :id"  # noqa: S608
            ),
            {"id": DEFAULT_PRACTICE_ID},
        ).first()
    # No registry row at all: boot has not run yet, so there is nothing stranded
    # in the template. Boot will create the row pointing at the new schema.
    return row is None or row[0] != DEFAULT_PRACTICE_SCHEMA


def migrate(engine: Engine, *, force: bool = False) -> list[OrphanCount]:
    """Move the deployment's charts onto ``practice_default``. Idempotent.

    Order is load-bearing and follows the design note: rename, then register,
    then RLS. Policies go on last, after the registry says the schema is a
    practice — enabling them earlier would arm the guards against a schema the
    rest of the system still thinks is the template.

    ``force`` skips the refusal, not the report. It exists because an operator
    who has looked at the numbers and decided that N orphaned rows are genuinely
    disposable should not have to edit the migration to proceed — but they have
    to say so, and the count still lands in the log.
    """
    if is_migrated(engine):
        logger.info("Single-practice migration: already migrated, nothing to do")
        return []

    counts = preflight(engine)
    lost = [c for c in counts if c.orphaned]
    if lost and not force:
        raise PreflightError(
            "Refusing to migrate: rows would become invisible under row-level "
            "security once policies apply.\n\n"
            + format_report(counts)
            + "\n\nEvery count must be zero. A patients row needs a live "
            "patient_clinicians grant; a row whose owning user_id is NULL has no "
            "principal at all. Fix the data, then re-run. Pass --force only if "
            "you have decided these rows are genuinely disposable."
        )
    if lost:
        logger.warning(
            "Single-practice migration: proceeding under --force with %d table(s) "
            "holding rows no principal can read",
            len(lost),
        )

    with engine.begin() as conn:
        # One transaction: a half-migrated deployment — schema renamed but
        # registry still naming the old one — is unreachable in a way that is
        # much harder to reason about than either end state.
        conn.execute(
            text(f"ALTER SCHEMA {DEFAULT_PRACTICE_SCHEMA} RENAME TO {DEFAULT_PRACTICE_OWN_SCHEMA}")
        )
        conn.execute(
            text(
                f"UPDATE {PLATFORM_SCHEMA}.practices SET schema_name = :new WHERE id = :id"  # noqa: S608 — schema/table are validated identifiers, never user input
            ),
            {"new": DEFAULT_PRACTICE_OWN_SCHEMA, "id": DEFAULT_PRACTICE_ID},
        )
        logger.info(
            "Single-practice migration: renamed '%s' to '%s' and re-pointed the registry",
            DEFAULT_PRACTICE_SCHEMA,
            DEFAULT_PRACTICE_OWN_SCHEMA,
        )

    # The template has to come back: it was the schema we just renamed away, and
    # every future practice is cloned from it. Outside the transaction above
    # because it runs its own DDL and stamping.
    from .provisioning import create_practice_schema

    create_practice_schema(engine, DEFAULT_PRACTICE_SCHEMA)
    logger.info("Single-practice migration: rebuilt the '%s' template", DEFAULT_PRACTICE_SCHEMA)

    from sqlalchemy.orm import Session

    with Session(engine) as session:
        # ``has_patient_access`` is a schema-local function: the policies below
        # call it unqualified, so it has to be on the search_path. It moved with
        # the rename — the renamed schema carries its own copy — so the path
        # names the NEW schema. Pointing at the rebuilt template instead would
        # resolve to a different function object with the same body, which works
        # right up until the two definitions diverge.
        session.execute(
            text(f"SET search_path = {DEFAULT_PRACTICE_OWN_SCHEMA}, {PLATFORM_SCHEMA}, public")
        )
        enable_rls_on_schema(session, DEFAULT_PRACTICE_OWN_SCHEMA)
        session.commit()
    logger.info(
        "Single-practice migration: row-level security applied to '%s'",
        DEFAULT_PRACTICE_OWN_SCHEMA,
    )
    return counts
