# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Reading a tenant template's row-level security out of its SQL.

``desired_from_sql`` is the only thing that stands between the committed file
and what the applier does to a live schema, so the first test here reads the
real ``tenant_template.sql`` and no hand-written stand-in. The others pin the
cases the real file does not happen to contain today.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from app.db.provisioning import TEMPLATE_ROW_SECURITY_MARKER
from app.db.rls_apply import (
    DesiredTable,
    desired_from_sql,
)
from app.db.rls_state import FINGERPRINT_PREFIX

_TEMPLATE = Path(__file__).resolve().parents[1] / "app" / "db" / "tenant_template.sql"

_TABLES = (
    """\
CREATE TABLE __TENANT_SCHEMA__.notes (
    id uuid NOT NULL
);

CREATE TABLE __TENANT_SCHEMA__.lookups (
    id uuid NOT NULL
);

"""
    + TEMPLATE_ROW_SECURITY_MARKER
)

_OWNER_POLICY = (
    "CREATE POLICY rls_owner ON __TENANT_SCHEMA__.notes "
    "USING (((user_id)::text = current_setting('app.current_user_id'::text, true)));"
)


def test_the_committed_template_reads_whole() -> None:
    """Every table, flag, policy and comment in the real file is accounted for."""
    sql = _TEMPLATE.read_text()
    desired = desired_from_sql(sql)

    lines = sql.splitlines()
    assert len(desired) == sum(line.startswith("CREATE TABLE ") for line in lines)
    forced = sum(line.endswith(" FORCE ROW LEVEL SECURITY;") for line in lines)
    assert forced > 0
    assert sum(table.forced for table in desired.values()) == forced
    assert sum(table.enabled for table in desired.values()) == forced

    policies = sum(len(table.policies) for table in desired.values())
    assert policies == sum(line.startswith("CREATE POLICY ") for line in lines)
    for name, table in desired.items():
        # A forced table with no policy denies every row; an unforced one with
        # a policy has a policy that does nothing.
        assert bool(table.policies) == table.forced, name
        assert set(table.comments) == set(table.policies), name
        assert all(c.startswith(FINGERPRINT_PREFIX) for c in table.comments.values()), name


def test_a_table_the_sql_creates_with_no_row_security_is_still_listed() -> None:
    desired = desired_from_sql(
        _TABLES + "\nALTER TABLE __TENANT_SCHEMA__.notes ENABLE ROW LEVEL SECURITY;\n"
    )
    assert desired["lookups"] == DesiredTable()
    assert desired["notes"].enabled
    assert not desired["notes"].forced


def test_a_policy_spanning_lines_is_kept_whole() -> None:
    policy = (
        "CREATE POLICY rls_purge ON __TENANT_SCHEMA__.notes FOR DELETE USING ((EXISTS ( SELECT 1\n"
        "   FROM __TENANT_SCHEMA__.patients p\n"
        "  WHERE (p.id = notes.patient_id))));"
    )
    desired = desired_from_sql(_TABLES + "\n" + policy + "\n")
    assert desired["notes"].policies == {"rls_purge": policy}


def test_a_later_drop_and_create_replaces_the_earlier_policy() -> None:
    replacement = _OWNER_POLICY.replace("USING (", "USING (true OR (")[:-1] + ");"
    desired = desired_from_sql(
        _TABLES + "\n" + _OWNER_POLICY + "\n",
        TEMPLATE_ROW_SECURITY_MARKER
        + "\nDROP POLICY rls_owner ON __TENANT_SCHEMA__.notes;\n\n"
        + replacement
        + "\n",
    )
    assert desired["notes"].policies == {"rls_owner": replacement}


def test_a_dropped_policy_takes_its_comment_with_it() -> None:
    desired = desired_from_sql(
        _TABLES
        + "\n"
        + _OWNER_POLICY
        + "\n\nCOMMENT ON POLICY rls_owner ON __TENANT_SCHEMA__.notes IS 'x';\n"
        + "\nDROP POLICY rls_owner ON __TENANT_SCHEMA__.notes;\n"
    )
    assert desired["notes"] == DesiredTable()


@pytest.mark.parametrize(
    "statement",
    [
        "ALTER TABLE __TENANT_SCHEMA__.elsewhere ENABLE ROW LEVEL SECURITY;",
        "CREATE POLICY p ON __TENANT_SCHEMA__.elsewhere USING (true);",
        "COMMENT ON POLICY rls_missing ON __TENANT_SCHEMA__.notes IS 'x';",
    ],
)
def test_row_security_for_something_the_sql_does_not_create_is_refused(statement: str) -> None:
    with pytest.raises(ValueError, match=r"creates|no such policy"):
        desired_from_sql(_TABLES + "\n" + statement + "\n")


@pytest.mark.parametrize(
    "statement",
    [
        # Disabling is said by leaving the flag out, never by a statement.
        "ALTER TABLE __TENANT_SCHEMA__.notes DISABLE ROW LEVEL SECURITY;",
        "COMMENT ON POLICY rls_owner ON __TENANT_SCHEMA__.notes IS 'it''s quoted';",
        "CREATE POLICY rls_owner ON practice.notes USING (true);",
        # Each of these, skipped, would read as "the file does not have it",
        # and the applier would take it away from every practice.
        "ALTER TABLE ONLY __TENANT_SCHEMA__.notes\n    ENABLE ROW LEVEL SECURITY;",
        "alter table __TENANT_SCHEMA__.notes enable row level security;",
        "create policy rls_other on __TENANT_SCHEMA__.notes using (true);",
        "ALTER POLICY rls_owner ON __TENANT_SCHEMA__.notes USING (true);",
        "  DROP POLICY rls_owner ON __TENANT_SCHEMA__.notes;",
    ],
)
def test_a_statement_it_cannot_read_is_refused_not_skipped(statement: str) -> None:
    with pytest.raises(ValueError, match=r"unreadable|qualified"):
        desired_from_sql(_TABLES + "\n" + _OWNER_POLICY + "\n\n" + statement + "\n")


def test_a_table_whose_name_contains_policy_is_not_a_policy_statement() -> None:
    """The real template has one, ``scheduling_policy``, with sequences and constraints."""
    sql = _TABLES.replace("lookups", "scheduling_policy") + (
        "\nALTER SEQUENCE __TENANT_SCHEMA__.scheduling_policy_id_seq "
        "OWNED BY __TENANT_SCHEMA__.scheduling_policy.id;\n"
    )
    assert desired_from_sql(sql)["scheduling_policy"] == DesiredTable()


def test_sql_with_no_marker_is_refused_rather_than_read_as_no_row_security() -> None:
    """The template's schema part alone names 83 tables and enables none.

    Read as the whole truth, that would turn row-level security off on every
    one of them. The real file's own first half is the input here.
    """
    schema_part, marker, _ = _TEMPLATE.read_text().partition(TEMPLATE_ROW_SECURITY_MARKER)
    assert marker
    assert "CREATE TABLE " in schema_part
    with pytest.raises(ValueError, match="no row-level security marker"):
        desired_from_sql(schema_part)
    with pytest.raises(ValueError, match="no row-level security marker"):
        desired_from_sql(_TEMPLATE.read_text(), schema_part)


def test_a_marker_with_nothing_after_it_does_mean_none() -> None:
    assert desired_from_sql(_TABLES) == {"notes": DesiredTable(), "lookups": DesiredTable()}


def test_a_second_statement_on_a_policys_line_is_refused() -> None:
    """The statement is run as written, so anything riding on it would run too."""
    smuggled = _OWNER_POLICY + " ALTER TABLE __TENANT_SCHEMA__.notes OWNER TO someone;"
    with pytest.raises(ValueError, match="more than one statement"):
        desired_from_sql(_TABLES + "\n" + smuggled + "\n")


def test_a_semicolon_inside_a_string_does_not_end_the_policy() -> None:
    policy = (
        "CREATE POLICY rls_owner ON __TENANT_SCHEMA__.notes USING ((kind = 'a;'::text) OR\n"
        "  (kind = 'it''s;'::text));"
    )
    assert desired_from_sql(_TABLES + "\n" + policy + "\n")["notes"].policies == {
        "rls_owner": policy
    }


def test_a_policy_that_never_ends_is_refused() -> None:
    with pytest.raises(ValueError, match="no end"):
        desired_from_sql(_TABLES + "\nCREATE POLICY p ON __TENANT_SCHEMA__.notes USING (true)\n")
