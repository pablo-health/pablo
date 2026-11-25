# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Every policy and flag on every template table survives repair."""

from __future__ import annotations

import os
import re
from typing import TYPE_CHECKING

import pytest
from app.db.rls_apply import (
    DesiredTable,
    apply_tenant_rls,
    tenant_rls_differences,
)
from app.db.rls_state import read_live_rls
from sqlalchemy.orm import Session as OrmSession

if TYPE_CHECKING:
    from collections.abc import Iterator

    from app.db.rls_state import LiveRls
    from sqlalchemy.engine import Engine

from .helpers import (
    _TEMPLATE_TABLES,
)

_DB_URL = os.environ.get("DATABASE_URL", "")


pytestmark = pytest.mark.skipif(
    not _DB_URL or os.environ.get("DATABASE_BACKEND") != "postgres",
    reason=(
        "PostgreSQL not configured. Set DATABASE_URL and DATABASE_BACKEND=postgres "
        "or run via make test-integration."
    ),
)


_DAMAGED_BODY = "(('damaged'::text) = ('by hand'::text))"


_POLICY_HEAD = re.compile(r"^(CREATE POLICY \w+ ON \S+(?: AS RESTRICTIVE)?)( FOR \w+)?")


def _with_another_command(statement: str) -> str:
    """The same policy for a different command: all of them, or only UPDATE.

    UPDATE because it is the one single command that takes both USING and
    WITH CHECK, so any policy for every command can be narrowed to it.
    """
    head = _POLICY_HEAD.match(statement)
    assert head is not None, statement
    replacement = "" if head.group(2) else " FOR UPDATE"
    return head.group(1) + replacement + statement[head.end() :]


def _damages(schema: str, table: str, desired: DesiredTable) -> Iterator[tuple[str, list[str]]]:
    """Each way one table's row-level security can be made wrong, one at a time."""
    qualified = f'"{schema}"."{table}"'
    for policy, statement in sorted(desired.policies.items()):
        drop = f'DROP POLICY "{policy}" ON {qualified}'
        yield f"{policy} dropped", [drop]
        clause = "WITH CHECK" if " USING (" not in statement else "USING"
        yield (
            f"{policy} body edited",
            [f'ALTER POLICY "{policy}" ON {qualified} {clause} ({_DAMAGED_BODY})'],
        )
        recreated = _with_another_command(statement).replace("__TENANT_SCHEMA__", schema)
        yield f"{policy} for another command", [drop, recreated.rstrip(";")]
    yield "a policy added", [f"CREATE POLICY zz_added ON {qualified} USING (true)"]
    if desired.enabled:
        yield "row security disabled", [f"ALTER TABLE {qualified} DISABLE ROW LEVEL SECURITY"]
    else:
        yield "row security enabled", [f"ALTER TABLE {qualified} ENABLE ROW LEVEL SECURITY"]
    if desired.forced:
        yield "no longer forced", [f"ALTER TABLE {qualified} NO FORCE ROW LEVEL SECURITY"]
    else:
        yield "forced", [f"ALTER TABLE {qualified} FORCE ROW LEVEL SECURITY"]


@pytest.mark.parametrize("table", _TEMPLATE_TABLES)
def test_every_single_damage_to_this_table_is_found_and_undone(
    engine: Engine, desired: dict[str, DesiredTable], settled: tuple[str, LiveRls], table: str
) -> None:
    """Not a sample. Each table in the template, and each policy on it, damaged each way.

    For every damage: the read-only report names this table and no other, one
    pass puts it right, and the schema is back to exactly what it was. The
    tests above do this to ``patients``; a bug that only shows on some other
    policy's shape would get past them and cannot get past this.

    One test per table, so a failure names the table and the run stays inside
    the suite's time limit for a single test.
    """
    schema, holds = settled
    failures: list[str] = []
    cases = 0
    with OrmSession(engine) as session:
        assert read_live_rls(session, schema) == holds, (
            "the schema was not as it should be before this table was touched; "
            "an earlier table's test left it damaged"
        )
        for what, statements in _damages(schema, table, desired[table]):
            cases += 1
            cursor = session.connection().connection.cursor()
            for statement in statements:
                cursor.execute(statement)
            session.commit()

            found = tenant_rls_differences(session, schema, desired)
            if list(found) != [table]:
                failures.append(f"{what}: reported {sorted(found)}")
            apply_tenant_rls(session, schema, desired)
            session.commit()
            if read_live_rls(session, schema) != holds:
                failures.append(f"{what}: not put back")
                break
    assert not failures, f"{table}: {len(failures)} of {cases} damages got through: {failures}"
    assert cases == 3 * len(desired[table].policies) + 3
