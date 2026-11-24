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

**How a difference is found.** Each live policy is rendered the way ``pg_dump``
writes one and compared with the file's statement, which ``pg_dump`` also
wrote. Equal text means the policy is the file's. That catches a policy edited
by hand (``ALTER POLICY``), which a comment on the policy would not: the
comment survives the edit.

Equal text is not the only way to be right, though. Postgres does not always
store a statement in the form it would print it back: applying
``= ANY ((ARRAY['a'::character varying])::text[])`` stores
``= ANY (ARRAY[('a'::character varying)::text])``, which means the same and
reads differently. So a policy created from the file can differ from the file
as text for ever. When the applier creates a policy and reads back something
other than what it ran, it records a hash of the two together as the policy's
comment (``rls-applied-v1-…``). On the next pass that hash matching is the
second way to be right: this is exactly what the file's statement turned into
here. An edit by hand changes what is stored, so the hash stops matching.

A server whose deparser differs from the one the template was captured on is
the same case, and is handled the same way: one rebuild, then the recorded
hash. So is a policy that names roles, which ``pg_dump`` may list in another
order.

**What this is not.** It finds drift: a schema older than a policy, a policy
the file has lost, an edit made by hand. It is not a defence against someone
who can run DDL on the schema. The hash has no secret in it, so they could
write a matching comment beside an edit; they could also change a policy the
moment a pass has finished.

**What "altered" counts.** Tables whose flags or policies ended up different.
A policy dropped and created again into exactly what it was, to record its
hash, is not a change and is not counted.

Nothing here commits. The caller owns the transaction.
"""

from __future__ import annotations

import hashlib
import logging
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, NamedTuple

from .rls_state import SCHEMA_PLACEHOLDER, LivePolicy, LiveRls, read_live_rls, schema_neutral

if TYPE_CHECKING:
    from collections.abc import Iterable, Iterator, Mapping

    from sqlalchemy.orm import Session

    from . import RlsReconcileCounts

logger = logging.getLogger(__name__)

# One statement per pattern, each as ``pg_dump`` writes it: starting in column
# 0, names qualified with the schema placeholder.
_TABLE = re.compile(r"^CREATE TABLE (\S+) \($")
_RLS_FLAG = re.compile(r"^ALTER TABLE (?:ONLY )?(\S+) (ENABLE|FORCE) ROW LEVEL SECURITY;$")
_POLICY_START = re.compile(r"^CREATE POLICY (\w+) ON (\S+) ")
_POLICY_DROP = re.compile(r"^DROP POLICY (?:IF EXISTS )?(\w+) ON (\S+);$")
_POLICY_COMMENT = re.compile(r"^COMMENT ON POLICY (\w+) ON (\S+) IS '([^']*)';$")
# Anything that is about a policy or a table's row-level security, however it
# is spelled. ``\bPOLICY\b`` alone would catch a table named ``scheduling_policy``.
_UNREAD_ROW_SECURITY = re.compile(
    r"^\s*(?:CREATE|ALTER|DROP)\s+POLICY\b|^\s*COMMENT\s+ON\s+POLICY\b|ROW\s+LEVEL\s+SECURITY",
    re.IGNORECASE,
)

_QUALIFIER = f"{SCHEMA_PLACEHOLDER}."

#: The comment on a policy whose stored form differs from the statement that
#: created it; see the module docstring. Versioned like the reconcile's own
#: prefix, so a change to what is hashed invalidates every recorded value.
APPLIED_PREFIX = "rls-applied-v1-"

#: ``pg_policy.polcmd`` to the clause ``pg_dump`` writes for it.
_COMMAND_CLAUSE = {
    "*": "",
    "r": " FOR SELECT",
    "a": " FOR INSERT",
    "w": " FOR UPDATE",
    "d": " FOR DELETE",
}


@dataclass
class DesiredTable:
    """One table's row-level security as the SQL states it."""

    enabled: bool = False
    forced: bool = False
    #: ``{policy name: CREATE POLICY statement}``, schema placeholder intact.
    policies: dict[str, str] = field(default_factory=dict)
    #: ``{policy name: comment}`` for the policies the SQL comments on.
    comments: dict[str, str] = field(default_factory=dict)


def desired_from_sql(*sql_texts: str) -> dict[str, DesiredTable]:
    """What the given SQL says each table's row-level security should be.

    Each text is a tenant template: ``pg_dump`` output with every name
    qualified by the schema placeholder. A table appears in the result if a
    text creates it, whether or not it has any row-level security, because
    "the file says none" is a state to enforce too. Statements are read in
    order, so a later ``DROP POLICY`` or ``CREATE POLICY`` replaces an earlier
    one of the same name.

    Raises ``ValueError`` on a row-level security statement it cannot read, or
    one that names a table no text creates: applying half of a file would
    leave a schema in a state nobody wrote down.

    Every text must carry the template's row-level security marker. A file
    without one says nothing about row-level security, and reading that as
    "none" would turn it off on every table the file creates. A file that
    means none says so with the marker and an empty section after it.
    """
    from .provisioning import TEMPLATE_ROW_SECURITY_MARKER

    for sql in sql_texts:
        if TEMPLATE_ROW_SECURITY_MARKER not in sql:
            msg = (
                "SQL with no row-level security marker "
                f"({TEMPLATE_ROW_SECURITY_MARKER.strip()!r}): refusing to read it as "
                "'no row-level security on any of its tables'"
            )
            raise ValueError(msg)
    statements = [found for sql in sql_texts for found in _row_security_statements(sql)]

    # Tables first: a file's row-level security section comes after its tables,
    # but a second file may add to a table the first one created.
    tables = {s.table: DesiredTable() for s in statements if s.kind == "TABLE"}
    for statement in statements:
        if statement.kind == "TABLE":
            continue
        desired = tables.get(statement.table)
        if desired is None:
            msg = f"row-level security for {statement.table!r}, which none of the SQL creates"
            raise ValueError(msg)
        _record(desired, statement)

    for table_name, desired in tables.items():
        stray = sorted(set(desired.comments) - set(desired.policies))
        if stray:
            msg = f"{table_name}: a comment on {stray}, with no such policy in the SQL"
            raise ValueError(msg)
    return tables


class _Statement(NamedTuple):
    #: ``TABLE``, ``ENABLE``, ``FORCE``, ``CREATE``, ``DROP`` or ``COMMENT``.
    kind: str
    table: str
    policy: str = ""
    #: The whole statement for ``CREATE``, the comment's text for ``COMMENT``.
    value: str = ""


def _record(desired: DesiredTable, statement: _Statement) -> None:
    if statement.kind == "ENABLE":
        desired.enabled = True
    elif statement.kind == "FORCE":
        desired.forced = True
    elif statement.kind == "CREATE":
        desired.policies[statement.policy] = statement.value
    elif statement.kind == "DROP":
        desired.policies.pop(statement.policy, None)
        desired.comments.pop(statement.policy, None)
    else:
        desired.comments[statement.policy] = statement.value


def _row_security_statements(sql: str) -> Iterator[_Statement]:
    """Every table and row-level security statement in ``sql``, in order."""
    lines = sql.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]
        i += 1
        if created := _TABLE.match(line):
            yield _Statement("TABLE", _table_name(created.group(1), line))
        elif flag := _RLS_FLAG.match(line):
            yield _Statement(flag.group(2), _table_name(flag.group(1), line))
        elif policy := _POLICY_START.match(line):
            whole = line
            while not _is_one_statement(whole):
                if i == len(lines):
                    msg = f"a policy with no end: {line[:120]!r}"
                    raise ValueError(msg)
                whole += "\n" + lines[i]
                i += 1
            yield _Statement("CREATE", _table_name(policy.group(2), line), policy.group(1), whole)
        elif dropped := _POLICY_DROP.match(line):
            yield _Statement("DROP", _table_name(dropped.group(2), line), dropped.group(1))
        elif comment := _POLICY_COMMENT.match(line):
            table = _table_name(comment.group(2), line)
            yield _Statement("COMMENT", table, comment.group(1), comment.group(3))
        elif _is_row_security(line):
            msg = f"unreadable row-level security statement: {line[:120]!r}"
            raise ValueError(msg)


def _is_one_statement(sql: str) -> bool:
    """Whether ``sql`` is exactly one statement, ended by its last character.

    ``False`` while it has no ``;`` outside a string literal yet, so the caller
    reads another line. A ``;`` anywhere but the end is refused: the text is
    run as it stands, and a second statement riding on a policy's line would
    run with it, unread.
    """
    in_string = False
    ends: list[int] = []
    for position, char in enumerate(sql):
        if char == "'":
            # A doubled quote inside a literal flips twice and changes nothing.
            in_string = not in_string
        elif char == ";" and not in_string:
            ends.append(position)
    if not ends:
        return False
    if ends != [len(sql.rstrip()) - 1]:
        msg = f"more than one statement where one policy was expected: {sql[:120]!r}"
        raise ValueError(msg)
    return True


def _is_row_security(line: str) -> bool:
    """Whether a line no pattern matched is one that should have been read.

    Deliberately wide, and blind to case and spacing. A row-level security
    statement that is skipped reads as "the file does not have this", and the
    applier acts on that by removing it from every practice.
    """
    return _UNREAD_ROW_SECURITY.search(line) is not None


def _table_name(qualified: str, line: str) -> str:
    if not qualified.startswith(_QUALIFIER) or '"' in qualified:
        msg = f"expected a table qualified with {_QUALIFIER!r}: {line[:120]!r}"
        raise ValueError(msg)
    return qualified[len(_QUALIFIER) :]


def render_policy(schema_name: str, table_name: str, policy_name: str, policy: LivePolicy) -> str:
    """A live policy as ``pg_dump`` would write it, schema placeholder in place.

    The same form the template's statements are in, so the two can be compared
    as text. ``policy`` must have been read with ``read_live_rls``, which pins
    the search path so every name is qualified the way ``pg_dump`` qualifies it.
    """
    statement = f"CREATE POLICY {policy_name} ON {_QUALIFIER}{table_name}"
    if not policy.permissive:
        statement += " AS RESTRICTIVE"
    statement += _COMMAND_CLAUSE[policy.command]
    if policy.roles != "public":
        statement += " TO " + ", ".join(policy.roles.split(","))
    if policy.using is not None:
        statement += f" USING ({schema_neutral(policy.using, schema_name)})"
    if policy.with_check is not None:
        statement += f" WITH CHECK ({schema_neutral(policy.with_check, schema_name)})"
    return statement + ";"


def applied_fingerprint(statement: str, stored: str) -> str:
    """The comment that says ``stored`` is what ``statement`` became on this server.

    Both are schema-neutral: ``statement`` is the file's, ``stored`` is
    :func:`render_policy` of the policy it created.
    """
    digest = hashlib.sha256(f"{statement}\n--\n{stored}".encode()).hexdigest()
    return APPLIED_PREFIX + digest


def _ident(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _plan_table(
    schema_name: str,
    table_name: str,
    desired: DesiredTable,
    flags: tuple[bool, bool],
    live: Mapping[str, LivePolicy],
) -> tuple[list[str], list[str]]:
    """What takes one table from its live state to the desired one.

    Returns the statements, and the names of the policies they create.
    """
    qualified = f"{_ident(schema_name)}.{_ident(table_name)}"
    statements: list[str] = []
    created: list[str] = []

    if desired.enabled != flags[0]:
        verb = "ENABLE" if desired.enabled else "DISABLE"
        statements.append(f"ALTER TABLE {qualified} {verb} ROW LEVEL SECURITY")
    if desired.forced != flags[1]:
        verb = "FORCE" if desired.forced else "NO FORCE"
        statements.append(f"ALTER TABLE {qualified} {verb} ROW LEVEL SECURITY")

    statements.extend(
        f"DROP POLICY {_ident(name)} ON {qualified}"
        for name in sorted(live)
        if name not in desired.policies
    )

    for name in sorted(desired.policies):
        wanted = desired.policies[name]
        current = live.get(name)
        if current is not None:
            stored = render_policy(schema_name, table_name, name, current)
            if stored == wanted or current.comment == applied_fingerprint(wanted, stored):
                continue
            statements.append(f"DROP POLICY {_ident(name)} ON {qualified}")
        statements.append(wanted.replace(SCHEMA_PLACEHOLDER, schema_name).rstrip().rstrip(";"))
        created.append(name)
        comment = desired.comments.get(name)
        if comment is not None:
            # ``comment`` came through ``_POLICY_COMMENT``, which admits no quote.
            statements.append(f"COMMENT ON POLICY {_ident(name)} ON {qualified} IS '{comment}'")
    return statements, created


@dataclass
class _SchemaPlan:
    """Everything one pass would do to a schema, worked out from two catalog reads."""

    before: LiveRls
    #: ``{table: statements}`` for the tables that need any, in table order.
    statements: dict[str, list[str]] = field(default_factory=dict)
    #: ``{table: policy names}`` the statements create.
    created: dict[str, list[str]] = field(default_factory=dict)
    #: How many of the SQL's tables are in the schema.
    checked: int = 0
    #: Tables in the schema that no SQL creates.
    unknown: list[str] = field(default_factory=list)
    #: Tables the SQL creates that the schema does not have.
    missing: list[str] = field(default_factory=list)


def _plan_schema(
    session: Session,
    schema_name: str,
    desired: Mapping[str, DesiredTable],
) -> _SchemaPlan:
    plan = _SchemaPlan(before=read_live_rls(session, schema_name))
    for table_name in sorted(desired):
        flags = plan.before.flags.get(table_name)
        if flags is None:
            plan.missing.append(table_name)
            continue
        plan.checked += 1
        statements, created = _plan_table(
            schema_name,
            table_name,
            desired[table_name],
            flags,
            plan.before.policies.get(table_name, {}),
        )
        if statements:
            plan.statements[table_name] = statements
            plan.created[table_name] = created
    plan.unknown = sorted(set(plan.before.flags) - set(desired))
    return plan


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
    return _plan_schema(session, schema_name, desired).statements


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
    plan = _plan_schema(session, schema_name, desired)

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
        qualified = f"{_ident(schema_name)}.{_ident(table_name)}"
        for name in names:
            wanted = desired[table_name].policies[name]
            stored = render_policy(schema_name, table_name, name, after.policies[table_name][name])
            if stored != wanted:
                value = applied_fingerprint(wanted, stored)
                comments.append(f"COMMENT ON POLICY {_ident(name)} ON {qualified} IS '{value}'")
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
        try:
            for statement in statements:
                cursor.execute(statement)
        finally:
            cursor.execute("SELECT pg_catalog.set_config('search_path', %s, true)", (previous,))
    finally:
        cursor.close()
