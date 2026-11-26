# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Drop a table from the practice schema being migrated, and from no other.

The tenant chain runs with ``search_path = <practice>, platform, public``, so an
unqualified drop of a name the practice lacks resolves to ``platform``. Use this
for any tenant-chain drop of a name ``platform`` also holds. No CASCADE: drop
children first.
"""

from __future__ import annotations

_DROP = """
DO $$
DECLARE
    schema_name text := current_schema();
BEGIN
    IF schema_name IS NULL OR schema_name = 'platform' THEN
        RAISE NOTICE 'refusing to drop <table> outside a practice schema (%)', schema_name;
        RETURN;
    END IF;
    IF EXISTS (
        SELECT 1 FROM information_schema.tables
        WHERE table_schema = schema_name AND table_name = '<table>'
    ) THEN
        EXECUTE format('DROP TABLE %I.<table>', schema_name);
    END IF;
END
$$
"""


def drop_practice_table_sql(table: str) -> str:
    """SQL that drops ``table`` from ``current_schema()`` if it is there."""
    # ``replace`` on a constant: callers pass a literal table name, never input.
    if not table.isidentifier() or table.lower() != table:
        raise ValueError(f"not a plain table name: {table!r}")
    return _DROP.replace("<table>", table)
