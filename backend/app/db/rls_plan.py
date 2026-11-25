# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Compare a schema's live row-level security with the file's, and plan the DDL.

``rls_apply`` runs what this plans; nothing here touches a schema.

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
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from .rls_state import SCHEMA_PLACEHOLDER, LivePolicy, LiveRls, read_live_rls, schema_neutral

if TYPE_CHECKING:
    from collections.abc import Mapping

    from sqlalchemy.orm import Session

    from .rls_desired import DesiredTable

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


def quote_identifier(name: str) -> str:
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
    qualified = f"{quote_identifier(schema_name)}.{quote_identifier(table_name)}"
    statements: list[str] = []
    created: list[str] = []

    if desired.enabled != flags[0]:
        verb = "ENABLE" if desired.enabled else "DISABLE"
        statements.append(f"ALTER TABLE {qualified} {verb} ROW LEVEL SECURITY")
    if desired.forced != flags[1]:
        verb = "FORCE" if desired.forced else "NO FORCE"
        statements.append(f"ALTER TABLE {qualified} {verb} ROW LEVEL SECURITY")

    statements.extend(
        f"DROP POLICY {quote_identifier(name)} ON {qualified}"
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
            statements.append(f"DROP POLICY {quote_identifier(name)} ON {qualified}")
        statements.append(wanted.replace(SCHEMA_PLACEHOLDER, schema_name).rstrip().rstrip(";"))
        created.append(name)
        comment = desired.comments.get(name)
        if comment is not None:
            # ``comment`` came through ``_POLICY_COMMENT``, which admits no quote.
            statements.append(
                f"COMMENT ON POLICY {quote_identifier(name)} ON {qualified} IS '{comment}'"
            )
    return statements, created


@dataclass
class SchemaPlan:
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


def plan_schema(
    session: Session,
    schema_name: str,
    desired: Mapping[str, DesiredTable],
) -> SchemaPlan:
    plan = SchemaPlan(before=read_live_rls(session, schema_name))
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
