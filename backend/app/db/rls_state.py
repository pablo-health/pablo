# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Read a practice schema's row-level security state, and say whether a table matches.

``enable_rls_on_schema`` decides what every tenant table's row-level security
should be. This module is how it finds out what the table already has, so a
schema that is already right is verified rather than rebuilt.

**What "already right" means.** A row-scoped table must have RLS enabled and
forced, must carry exactly the policies the reconcile would create among the
names it manages, and each of those policies must carry the fingerprint the
reconcile wrote when it created them. A not-row-scoped table must have RLS
disabled. Anything else is rebuilt with the full drop-and-recreate pass.

**Why a fingerprint rather than comparing policy text.** The reconcile writes a
policy as source text and Postgres stores it as a parse tree; reading it back
gives the deparsed form, with casts and parentheses added. The two cannot be
compared textually, and no catalog function turns source text into the stored
form without running the DDL. So when the reconcile creates a table's
policies, it hashes two things together and records the hash as a comment on
each policy:

* the source statements it ran, so a change to a policy in code no longer
  matches and the table is rebuilt on the next pass;
* the deparsed policies Postgres stored from them, so a policy altered by
  hand afterwards (``ALTER POLICY``) no longer matches either.

A missing policy, an extra one under a managed name, or a policy created by
anything other than the reconcile (no fingerprint) also fails the check. Every
failure goes to the full rebuild, so the cost of a false mismatch is a slower
pass, never a weaker one.

The hash is schema-neutral: every ``<schema>.`` qualifier is replaced with the
same placeholder the tenant template uses, so a fingerprint captured in the
template is valid in every schema provisioned from it. The deparsed form is
read with ``search_path`` set to ``pg_catalog`` so every name is qualified the
same way whatever path the caller's session had. It still depends on the
server's deparser, so a server on a different major version from the one the
template was captured on simply rebuilds once and fingerprints its own output.
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import TYPE_CHECKING, NamedTuple

from sqlalchemy import text

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

#: Written as the comment on every policy the reconcile creates. The version
#: is part of the prefix so a change to what the hash covers invalidates every
#: stored fingerprint at once, rather than being compared like for like.
FINGERPRINT_PREFIX = "rls-fingerprint-v1-"

#: The schema stand-in inside a fingerprint. The tenant template uses the
#: same one, which keeps a fingerprint in the template meaningful after the
#: template's own substitution.
SCHEMA_PLACEHOLDER = "__TENANT_SCHEMA__"

_POLICY_STATEMENT = re.compile(r"^(CREATE|DROP) POLICY (?:IF EXISTS )?(\w+) ON ")


class TablePlan(NamedTuple):
    """What the reconcile would run on one table, and what that leaves behind."""

    statements: tuple[str, ...]
    #: Row-scoped (enabled, forced, policied) or left with RLS disabled.
    row_scoped: bool
    #: For the log line when the table is (re)built.
    description: str

    @property
    def policies(self) -> frozenset[str]:
        """The policy names the statements create."""
        return frozenset(_policy_names(self.statements, "CREATE"))

    @property
    def managed(self) -> frozenset[str]:
        """Every policy name the statements drop or create.

        A name outside this set belongs to something else (the practice
        directory's read policy, for one) and is neither checked nor touched,
        exactly as the drop-and-recreate pass leaves it alone.
        """
        return frozenset(_policy_names(self.statements, "CREATE", "DROP"))


class LivePolicy(NamedTuple):
    """One policy as the catalog holds it."""

    command: str
    permissive: bool
    roles: str
    using: str | None
    with_check: str | None
    comment: str | None


class LiveRls(NamedTuple):
    """A schema's row-level security, read in two catalog queries."""

    #: ``{table: (relrowsecurity, relforcerowsecurity)}``
    flags: dict[str, tuple[bool, bool]]
    #: ``{table: {policy name: policy}}``
    policies: dict[str, dict[str, LivePolicy]]


def _policy_names(statements: tuple[str, ...], *verbs: str) -> set[str]:
    names: set[str] = set()
    for statement in statements:
        match = _POLICY_STATEMENT.match(statement)
        if match and match.group(1) in verbs:
            names.add(match.group(2))
    return names


def schema_neutral(sql: str, schema_name: str) -> str:
    """Replace every ``<schema_name>.`` qualifier with the placeholder."""
    return re.sub(rf"(?<![A-Za-z0-9_]){re.escape(schema_name)}\.", f"{SCHEMA_PLACEHOLDER}.", sql)


def read_live_rls(session: Session, schema_name: str) -> LiveRls:
    """Every table's RLS flags and every policy in the schema, deparsed.

    ``search_path`` is pinned to ``pg_catalog`` for the two reads, so that
    ``pg_get_expr`` qualifies every name, and put back before returning: the
    caller runs DDL in the same transaction that resolves names through it.
    """
    previous = session.execute(text("SELECT pg_catalog.current_setting('search_path')")).scalar()
    session.execute(text("SELECT pg_catalog.set_config('search_path', 'pg_catalog', true)"))
    try:
        flag_rows = session.execute(
            text(
                "SELECT c.relname, c.relrowsecurity, c.relforcerowsecurity "
                "FROM pg_catalog.pg_class c "
                "JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace "
                "WHERE n.nspname = :schema AND c.relkind IN ('r', 'p')"
            ),
            {"schema": schema_name},
        ).fetchall()
        policy_rows = session.execute(
            text(
                "SELECT c.relname, p.polname, p.polcmd::text, p.polpermissive, "
                "pg_catalog.array_to_string(ARRAY("
                "  SELECT CASE WHEN r = 0 THEN 'public' ELSE pg_catalog.pg_get_userbyid(r) END "
                "  FROM pg_catalog.unnest(p.polroles) AS r ORDER BY 1"
                "), ','), "
                "pg_catalog.pg_get_expr(p.polqual, p.polrelid), "
                "pg_catalog.pg_get_expr(p.polwithcheck, p.polrelid), "
                "pg_catalog.obj_description(p.oid, 'pg_policy') "
                "FROM pg_catalog.pg_policy p "
                "JOIN pg_catalog.pg_class c ON c.oid = p.polrelid "
                "JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace "
                "WHERE n.nspname = :schema"
            ),
            {"schema": schema_name},
        ).fetchall()
    finally:
        if previous is not None:
            session.execute(
                text("SELECT pg_catalog.set_config('search_path', :path, true)"),
                {"path": previous},
            )

    flags = {row[0]: (bool(row[1]), bool(row[2])) for row in flag_rows}
    policies: dict[str, dict[str, LivePolicy]] = {}
    for table, name, command, permissive, roles, using, with_check, comment in policy_rows:
        policies.setdefault(table, {})[name] = LivePolicy(
            command, bool(permissive), roles, using, with_check, comment
        )
    return LiveRls(flags, policies)


def fingerprint(
    schema_name: str, plan: TablePlan, table_policies: dict[str, LivePolicy]
) -> str | None:
    """The fingerprint for this plan over these stored policies.

    ``None`` when a policy the plan creates is not there to fingerprint.
    """
    stored = []
    for name in sorted(plan.policies):
        policy = table_policies.get(name)
        if policy is None:
            return None
        stored.append(
            [
                name,
                policy.command,
                policy.permissive,
                policy.roles,
                None if policy.using is None else schema_neutral(policy.using, schema_name),
                None
                if policy.with_check is None
                else schema_neutral(policy.with_check, schema_name),
            ]
        )
    created = [
        schema_neutral(statement, schema_name)
        for statement in plan.statements
        if statement.startswith("CREATE POLICY")
    ]
    payload = json.dumps({"created": created, "stored": stored}, separators=(",", ":"))
    return FINGERPRINT_PREFIX + hashlib.sha256(payload.encode()).hexdigest()


def table_matches(schema_name: str, table_name: str, plan: TablePlan, live: LiveRls) -> bool:
    """Whether the table already holds exactly what ``plan`` would leave it with."""
    flags = live.flags.get(table_name)
    if flags is None:
        return False
    if not plan.row_scoped:
        return not flags[0]
    if flags != (True, True):
        return False
    table_policies = live.policies.get(table_name, {})
    if {name for name in table_policies if name in plan.managed} != plan.policies:
        return False
    expected = fingerprint(schema_name, plan, table_policies)
    return expected is not None and all(
        table_policies[name].comment == expected for name in plan.policies
    )


def record_fingerprints(session: Session, schema_name: str, plans: dict[str, TablePlan]) -> None:
    """Fingerprint the policies just created for ``plans``' row-scoped tables."""
    row_scoped = {table: plan for table, plan in plans.items() if plan.row_scoped}
    if not row_scoped:
        return
    live = read_live_rls(session, schema_name)
    comments: list[str] = []
    for table_name, plan in row_scoped.items():
        table_policies = live.policies.get(table_name, {})
        value = fingerprint(schema_name, plan, table_policies)
        if value is None:
            # Not there to fingerprint, so the next pass rebuilds it again.
            continue
        # ``name`` comes from our own CREATE statement, ``schema_name`` is
        # validated by the caller, and ``value`` is a fixed prefix plus hex.
        comments.extend(
            f"COMMENT ON POLICY {name} ON {schema_name}.{table_name} IS '{value}'"
            for name in sorted(plan.policies)
        )
    if comments:
        # One round trip for the lot: these are the cheapest statements in
        # the pass and there is one per policy.
        session.execute(text("; ".join(comments)))
