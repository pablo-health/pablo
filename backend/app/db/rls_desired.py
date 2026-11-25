# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Read the desired row-level security from captured tenant SQL."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, NamedTuple

from .rls_state import SCHEMA_PLACEHOLDER

if TYPE_CHECKING:
    from collections.abc import Iterator

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
