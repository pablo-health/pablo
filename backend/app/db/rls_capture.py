# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""The row-level security section of a tenant template, cut from a schema dump.

``backend/scripts/regen_tenant_template.py`` builds a scratch practice schema,
gives it row-level security the way a practice gets it, and dumps it with
``pg_dump`` (comments included, since the policy fingerprints are comments).
This module turns that dump into the section that follows the template's
row-level security marker: every RLS flag, every policy the reconcile
fingerprinted with its fingerprint, and the practice directory's read
policies, all with the scratch schema's name replaced by the placeholder.

``tables`` narrows the section to the named tables. A deployment that adds
its own tenant tables captures a section for just those, from its own scratch
schema, and ships it as SQL beside the engine's template.

Text in, text out: nothing here touches a database.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from .practice_directory import DIRECTORY_POLICY
from .rls_state import FINGERPRINT_PREFIX, SCHEMA_PLACEHOLDER

if TYPE_CHECKING:
    from collections.abc import Callable, Collection

__all__ = ["row_security_section"]

_RLS_FLAG = re.compile(r"^ALTER TABLE (?:ONLY )?(\S+) (?:ENABLE|FORCE) ROW LEVEL SECURITY;$")
_POLICY_START = re.compile(r"^CREATE POLICY (\w+) ON (\S+) ")
_POLICY_COMMENT = re.compile(r"^COMMENT ON POLICY (\w+) ON (\S+) IS '([^']*)';$")


def _policies(sql: str) -> dict[tuple[str, str], str]:
    """Every ``CREATE POLICY`` statement in ``sql``, keyed on (policy, table).

    pg_dump writes one per entry, starting at column 0 and ending on the
    first line that ends with ``;``.
    """
    found: dict[tuple[str, str], str] = {}
    lines = sql.splitlines()
    i = 0
    while i < len(lines):
        match = _POLICY_START.match(lines[i])
        if not match:
            i += 1
            continue
        start = i
        while not lines[i].rstrip().endswith(";"):
            i += 1
        found[(match.group(1), match.group(2))] = "\n".join(lines[start : i + 1])
        i += 1
    return found


def _flags_and_fingerprints(
    dump: str, neutral: Callable[[str], str], wanted: Callable[[str], bool]
) -> tuple[list[str], dict[tuple[str, str], str]]:
    """The dump's RLS flag lines, and its fingerprint comments keyed on (policy, table).

    Both schema-neutral, and only for tables ``wanted`` accepts.
    """
    flags: list[str] = []
    fingerprinted: dict[tuple[str, str], str] = {}
    for line in dump.splitlines():
        flag = _RLS_FLAG.match(line)
        if flag:
            if wanted(neutral(flag.group(1))):
                flags.append(neutral(line))
            continue
        comment = _POLICY_COMMENT.match(line)
        if comment and comment.group(3).startswith(FINGERPRINT_PREFIX):
            table = neutral(comment.group(2))
            if wanted(table):
                fingerprinted[(comment.group(1), table)] = neutral(line)
    return flags, fingerprinted


def row_security_section(
    dump: str, schema: str, schema_part: str, *, tables: Collection[str] | None = None
) -> str:
    """The row-level security statements of ``schema``'s dump, schema-neutral.

    Kept: every RLS flag, every policy carrying a reconcile fingerprint
    together with that fingerprint, and the practice directory's read
    policies, which carry none. A policy ``schema_part`` (the template's
    schema half) already creates is not created twice: identical, only its
    fingerprint is added; different, it is dropped and replaced.

    ``tables`` keeps only statements on those tables, by bare name. ``None``
    keeps every table.

    Raises ``ValueError`` for a fingerprint with no policy beside it, and for
    a section that still names ``schema``: a policy would be pinned to the
    scratch schema instead of the one being provisioned.
    """
    qualifier = re.compile(rf"(?<![A-Za-z0-9_]){re.escape(schema)}\.")
    prefix = f"{SCHEMA_PLACEHOLDER}."

    def neutral(sql: str) -> str:
        return qualifier.sub(prefix, sql)

    def wanted(qualified_table: str) -> bool:
        return tables is None or qualified_table.removeprefix(prefix) in tables

    flags, fingerprinted = _flags_and_fingerprints(dump, neutral, wanted)
    existing = _policies(schema_part)
    statements = list(flags)
    for (name, table), captured in _policies(dump).items():
        key = (name, neutral(table))
        if not wanted(key[1]):
            continue
        fingerprint = fingerprinted.pop(key, None)
        if fingerprint is None and name != DIRECTORY_POLICY:
            continue
        create = neutral(captured)
        if key not in existing:
            statements.append(create)
        elif existing[key] != create:
            statements.extend([f"DROP POLICY {name} ON {key[1]};", create])
        if fingerprint is not None:
            statements.append(fingerprint)
    if fingerprinted:
        msg = f"fingerprints with no policy in the dump: {sorted(fingerprinted)}"
        raise ValueError(msg)

    section = "\n\n".join(statements) + "\n"
    if re.search(rf"(?<![A-Za-z0-9_]){re.escape(schema)}(?![A-Za-z0-9_])", section):
        msg = (
            f"the row-level security section still names {schema}; a policy would "
            "be pinned to the capture schema instead of the one being provisioned"
        )
        raise ValueError(msg)
    return section
