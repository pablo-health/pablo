# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Guardrail tests: the Alembic chain has one head, and the tenant template tracks it.

The checks live in ``.claude/skills/migration-lint/check.py`` — pure stdlib, no
database and no container — so the same logic runs in CI, in a pre-push hook,
and by hand. This file pins the two parsing decisions the checks turn on, both
of which were learned from real failures:

* A fork in the chain is invisible to a file-overlap review, because the two
  migrations are two different files. It has to be found structurally.
* A bare table name is ambiguous. ``users`` has named both a platform table and
  a per-tenant one, so a migration passing ``schema="platform"`` must not be
  read as touching the tenant schema — that misread is a false positive that
  would block an unrelated pull request.

The end-to-end behaviour (real repo, real diff) is covered by the CI job that
runs the script directly; these are the unit-level pins beneath it.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

_SCRIPT = Path(__file__).resolve().parents[2] / ".claude" / "skills" / "migration-lint" / "check.py"
_spec = importlib.util.spec_from_file_location("migration_lint_check", _SCRIPT)
assert _spec is not None
assert _spec.loader is not None
lint = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(lint)


def _chain(*entries: tuple[str, list[str]]) -> dict[str, tuple[list[str], str]]:
    return {rev: (parents, f"{rev}_test.py") for rev, parents in entries}


class TestSingleHead:
    def test_linear_chain_passes(self) -> None:
        chain = _chain(("a", []), ("b", ["a"]), ("c", ["b"]))
        assert lint._check_single_head(chain) == 0

    def test_fork_fails(self) -> None:
        # Two branches cut from "a" — exactly the shape that leaves alembic
        # with two heads after both merge.
        chain = _chain(("a", []), ("b", ["a"]), ("c", ["a"]))
        assert lint._check_single_head(chain) == 1

    def test_merge_revision_rejoins_the_chain(self) -> None:
        # A merge revision names both parents, so the fork is resolved and the
        # merge point is the single head.
        chain = _chain(("a", []), ("b", ["a"]), ("c", ["a"]), ("m", ["b", "c"]))
        assert lint._check_single_head(chain) == 0

    def test_empty_chain_is_not_a_failure(self) -> None:
        assert lint._check_single_head({}) == 0

    def test_cycle_fails(self) -> None:
        chain = _chain(("a", ["b"]), ("b", ["a"]))
        assert lint._check_single_head(chain) == 1

    def test_real_repository_has_one_head(self) -> None:
        # The check that actually matters: main must never carry a fork.
        assert lint._check_single_head(lint._load_chain()) == 0


class TestTablesTouched:
    def test_op_add_column_is_tenant_scoped_by_default(self) -> None:
        body = 'op.add_column("patients", sa.Column("origin", sa.String(20)))'
        assert lint._tables_touched(body) == {"patients"}

    def test_explicit_platform_schema_is_excluded(self) -> None:
        # The false positive this guards: platform.users vs a per-tenant users.
        body = (
            "op.add_column(\n"
            '    "users",\n'
            '    sa.Column("profile_basics_completed_at", sa.DateTime(timezone=True)),\n'
            '    schema="platform",\n'
            ")"
        )
        assert lint._tables_touched(body) == set()

    def test_schema_kwarg_does_not_leak_to_the_next_call(self) -> None:
        body = (
            'op.add_column("users", sa.Column("a", sa.String()), schema="platform")\n'
            'op.add_column("patients", sa.Column("b", sa.String()))\n'
        )
        assert lint._tables_touched(body) == {"patients"}

    def test_raw_sql_alter_table(self) -> None:
        body = (
            'op.execute("ALTER TABLE appointments ADD COLUMN IF NOT EXISTS note_type VARCHAR(30)")'
        )
        assert lint._tables_touched(body) == {"appointments"}

    def test_raw_sql_platform_qualified_is_excluded(self) -> None:
        body = 'op.execute("ALTER TABLE platform.users ADD COLUMN foo TEXT")'
        assert lint._tables_touched(body) == set()

    def test_create_and_drop_table(self) -> None:
        body = (
            'op.execute("CREATE TABLE IF NOT EXISTS widgets (id uuid)")\n'
            'op.execute("DROP TABLE IF EXISTS gadgets")\n'
        )
        assert lint._tables_touched(body) == {"widgets", "gadgets"}


class TestTenantTables:
    def test_template_parses_to_a_nonempty_table_set(self) -> None:
        tables = lint._tenant_tables()
        assert tables, "tenant_template.sql should define tables"
        # Spot-check the two tables this morning's misses landed on.
        assert "patients" in tables
        assert "appointments" in tables

    def test_platform_only_tables_are_absent(self) -> None:
        # The template dumps the practice schema only, so platform-owned tables
        # must not appear — that absence is what makes check 3 safe to run.
        assert "practices" not in lint._tenant_tables()
