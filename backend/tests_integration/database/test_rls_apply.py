# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""The applier makes a practice schema's row-level security match the template.

``apply_tenant_rls`` reads nothing but the committed ``tenant_template.sql``
and the live catalog. These tests damage a provisioned practice in each way a
real one drifts, run the applier, and check both the catalog and what a
clinician can then read:

* a policy is missing (a practice older than the policy);
* a policy is there that the file does not have (one the file has since lost);
* a policy was edited by hand, which leaves its name and comment as they were;
* a table the file leaves unscoped has row-level security forced on;
* every policy is gone.

And the two things it must not do: touch a table no file creates, or change
anything on a schema that is already right. The second is the equivalence the
whole design rests on: a practice the existing reconcile has just finished
with must need nothing from the applier.

Runs as the ``pablo`` role, which the integration conftest creates
``NOSUPERUSER NOBYPASSRLS``; without that the read assertions would pass
against no policy at all. Run: ``make test-integration``.
"""

from __future__ import annotations

import os
import re
import uuid
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from app.db.rls_apply import (
    APPLIED_PREFIX,
    DesiredTable,
    apply_tenant_rls,
    desired_from_sql,
    tenant_rls_differences,
)
from app.db.rls_state import read_live_rls
from sqlalchemy import text
from sqlalchemy.orm import Session as OrmSession

if TYPE_CHECKING:
    from collections.abc import Iterator

    from app.db import RlsReconcileCounts
    from sqlalchemy.engine import Engine

_DB_URL = os.environ.get("DATABASE_URL", "")
pytestmark = pytest.mark.skipif(
    not _DB_URL or os.environ.get("DATABASE_BACKEND") != "postgres",
    reason=(
        "PostgreSQL not configured. Set DATABASE_URL and DATABASE_BACKEND=postgres "
        "or run via make test-integration."
    ),
)

_TEMPLATE = Path(__file__).resolve().parents[2] / "app" / "db" / "tenant_template.sql"

# Holds a grant on both seeded patients.
_TREATING = "3a7e1c82-45bd-5f19-8c62-0e4d7b3a9f15"
# A clinician in the same practice with a grant on neither.
_COLLEAGUE = "9c4f2b61-7d3e-5a80-b915-6e2a8d0c4f37"


@pytest.fixture(scope="module")
def desired() -> dict[str, DesiredTable]:
    return desired_from_sql(_TEMPLATE.read_text())


def _new_schema(engine: Engine, label: str) -> str:
    from app.db.provisioning import create_practice_schema  # noqa: PLC0415

    with engine.connect() as conn:
        conn.execute(text("SET search_path = practice, platform, public"))
        conn.commit()

    schema = f"practice_test_{label}_{uuid.uuid4().hex[:8]}"
    create_practice_schema(engine, schema)
    return schema


def _drop_schema(engine: Engine, schema: str) -> None:
    with engine.connect() as conn:
        conn.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        conn.commit()


@pytest.fixture(scope="module")
def reference(engine: Engine) -> Iterator[str]:
    """A practice provisioned today and never touched: the state to return to."""
    schema = _new_schema(engine, "apply_fresh")
    yield schema
    _drop_schema(engine, schema)


@pytest.fixture(scope="module")
def from_the_file(engine: Engine) -> Iterator[str]:
    """A schema built from the template's SQL and nothing else.

    No reconcile runs over it, so its policies are what Postgres makes of the
    file's own statements. For two tables that is not the text the file holds
    (see ``rls_apply``'s docstring), which is why this, and not ``reference``,
    is what a schema rebuilt by the applier is compared with.
    """
    from app.db.provisioning import (  # noqa: PLC0415
        TEMPLATE_ROW_SECURITY_MARKER,
        apply_template_sql,
    )

    schema = f"practice_test_apply_file_{uuid.uuid4().hex[:8]}"
    body, _, row_security = _TEMPLATE.read_text().partition(TEMPLATE_ROW_SECURITY_MARKER)
    apply_template_sql(engine, schema, body)
    apply_template_sql(engine, schema, row_security)
    yield schema
    _drop_schema(engine, schema)


@pytest.fixture
def practice(engine: Engine) -> Iterator[str]:
    """A practice with two patients, both treated by one clinician.

    Function-scoped: every test damages its schema.
    """
    schema = _new_schema(engine, "apply")
    with engine.begin() as conn:
        conn.execute(text(f"SET search_path = {schema}, platform, public"))
        conn.execute(text("SELECT set_config('app.current_user_id', :u, false)"), {"u": _TREATING})
        for first, last in (("Ada", "Lovelace"), ("Grace", "Hopper")):
            patient_id = str(uuid.uuid4())
            conn.execute(
                text(
                    "INSERT INTO patients (id, first_name, last_name, "
                    "first_name_lower, last_name_lower, status, session_count, "
                    "created_at, updated_at) "
                    "VALUES (CAST(:pid AS uuid), :first, :last, lower(:first), "
                    "lower(:last), 'active', 0, now(), now())"
                ),
                {"pid": patient_id, "first": first, "last": last},
            )
            conn.execute(
                text(
                    "INSERT INTO patient_clinicians (patient_id, user_id, granted_by) "
                    "VALUES (CAST(:pid AS uuid), :u, :u)"
                ),
                {"pid": patient_id, "u": _TREATING},
            )
    yield schema
    _drop_schema(engine, schema)


# The practice directory's read policy. The template carries it like any other,
# on the three tables the directory functions read.
_DIRECTORY_POLICY = "rls_practice_directory_read"
_DIRECTORY_TABLES = {"appointments", "patient_clinicians", "patients"}


def _apply(engine: Engine, schema: str, desired: dict[str, DesiredTable]) -> RlsReconcileCounts:
    with OrmSession(engine) as session:
        counts = apply_tenant_rls(session, schema, desired)
        session.commit()
    return counts


def _ddl(engine: Engine, statement: str) -> None:
    with engine.begin() as conn:
        conn.execute(text(statement))


def _patients_read_by(engine: Engine, schema: str, clinician: str) -> int:
    """How many patient rows *clinician* reads, with only their own id armed."""
    with engine.connect() as conn:
        conn.execute(text(f"SET search_path = {schema}, platform, public"))
        conn.execute(text("RESET app.current_patient_id"))
        conn.execute(text("SELECT set_config('app.current_user_id', :u, false)"), {"u": clinician})
        count = conn.execute(text("SELECT count(*) FROM patients")).scalar_one()
        conn.rollback()
    return int(count)


def _policies(engine: Engine, schema: str) -> dict[tuple[str, str], tuple[str, str, str, str]]:
    """Every policy in *schema*, keyed by (table, policy), schema name removed."""
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT tablename, policyname, permissive, cmd, "
                "COALESCE(qual, ''), COALESCE(with_check, '') "
                "FROM pg_policies WHERE schemaname = :s"
            ),
            {"s": schema},
        ).all()

    def strip(expr: str) -> str:
        return expr.replace(f'"{schema}".', "").replace(f"{schema}.", "")

    return {
        (table, policy): (permissive, cmd, strip(qual), strip(with_check))
        for table, policy, permissive, cmd, qual, with_check in rows
    }


def _policy_oids(engine: Engine, schema: str) -> dict[tuple[str, str], int]:
    """Each policy's OID. Dropping and creating a policy gives it a new one."""
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT c.relname, p.polname, p.oid FROM pg_policy p "
                "JOIN pg_class c ON c.oid = p.polrelid "
                "JOIN pg_namespace n ON n.oid = c.relnamespace WHERE n.nspname = :s"
            ),
            {"s": schema},
        ).all()
    return {(table, policy): int(oid) for table, policy, oid in rows}


def _flags(engine: Engine, schema: str) -> dict[str, tuple[bool, bool]]:
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT c.relname, c.relrowsecurity, c.relforcerowsecurity FROM pg_class c "
                "JOIN pg_namespace n ON n.oid = c.relnamespace "
                "WHERE n.nspname = :s AND c.relkind = 'r'"
            ),
            {"s": schema},
        ).all()
    return {name: (bool(enabled), bool(forced)) for name, enabled, forced in rows}


def test_the_connecting_role_does_not_bypass_rls(engine: Engine) -> None:
    """If this fails, every read assertion below is meaningless."""
    with engine.connect() as conn:
        row = conn.execute(
            text("SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname = current_user")
        ).one()
    assert not row[0]
    assert not row[1]


def test_a_practice_the_reconcile_just_finished_needs_nothing(
    engine: Engine, practice: str, desired: dict[str, DesiredTable]
) -> None:
    """Provisioning applied the template and ran the existing reconcile over it.

    If the applier finds anything to change here, it and the reconcile disagree
    about what the template means, and switching one for the other would change
    live policies. The OIDs say more than the count: not one policy was dropped
    and created again.
    """
    before = (_policies(engine, practice), _flags(engine, practice))
    oids = _policy_oids(engine, practice)

    counts = _apply(engine, practice, desired)

    assert counts.altered == 0
    assert counts.skipped == 0
    assert counts.applied == len(desired)
    assert _policy_oids(engine, practice) == oids
    assert (_policies(engine, practice), _flags(engine, practice)) == before


def test_the_directory_policy_is_the_files_like_any_other(
    engine: Engine, practice: str, desired: dict[str, DesiredTable]
) -> None:
    """It is in the template, so nothing has to be told to spare it.

    Dropped, it comes back; and the directory role is the only one it admits,
    which is the part of the statement a careless capture would lose.
    """
    assert {
        table for table, policy in _policies(engine, practice) if policy == _DIRECTORY_POLICY
    } == _DIRECTORY_TABLES
    for table in _DIRECTORY_TABLES:
        assert " TO pablo_practice_directory " in desired[table].policies[_DIRECTORY_POLICY]

    _ddl(engine, f'DROP POLICY {_DIRECTORY_POLICY} ON "{practice}".appointments')
    assert _apply(engine, practice, desired).altered == 1

    with engine.connect() as conn:
        roles = conn.execute(
            text(
                "SELECT roles FROM pg_policies "
                "WHERE schemaname = :s AND tablename = 'appointments' AND policyname = :p"
            ),
            {"s": practice, "p": _DIRECTORY_POLICY},
        ).scalar_one()
    assert roles == ["pablo_practice_directory"]


def test_a_missing_policy_is_created(
    engine: Engine, practice: str, desired: dict[str, DesiredTable]
) -> None:
    assert _patients_read_by(engine, practice, _TREATING) == 2
    _ddl(engine, f'DROP POLICY rls_patient_access ON "{practice}".patients')
    assert _patients_read_by(engine, practice, _TREATING) == 0, "the damage was not done"

    counts = _apply(engine, practice, desired)

    assert counts.altered == 1
    assert _patients_read_by(engine, practice, _TREATING) == 2
    assert _patients_read_by(engine, practice, _COLLEAGUE) == 0


def test_a_policy_the_sql_does_not_have_is_dropped(
    engine: Engine, practice: str, desired: dict[str, DesiredTable]
) -> None:
    _ddl(engine, f'CREATE POLICY rls_everyone ON "{practice}".patients FOR SELECT USING (true)')
    assert _patients_read_by(engine, practice, _COLLEAGUE) == 2, "the damage was not done"

    counts = _apply(engine, practice, desired)

    assert counts.altered == 1
    assert _patients_read_by(engine, practice, _COLLEAGUE) == 0
    assert _patients_read_by(engine, practice, _TREATING) == 2
    assert ("patients", "rls_everyone") not in _policies(engine, practice)


def test_a_policy_edited_by_hand_is_put_back(
    engine: Engine, practice: str, reference: str, desired: dict[str, DesiredTable]
) -> None:
    """``ALTER POLICY`` keeps the name and the comment, so only the body shows it."""
    _ddl(engine, f'ALTER POLICY rls_patient_access ON "{practice}".patients USING (true)')
    assert _patients_read_by(engine, practice, _COLLEAGUE) == 2, "the damage was not done"

    counts = _apply(engine, practice, desired)

    assert counts.altered == 1
    assert _patients_read_by(engine, practice, _COLLEAGUE) == 0
    assert _patients_read_by(engine, practice, _TREATING) == 2
    assert _policies(engine, practice) == _policies(engine, reference)


def test_a_table_the_sql_leaves_unscoped_has_row_security_turned_off(
    engine: Engine, practice: str, desired: dict[str, DesiredTable]
) -> None:
    live = _flags(engine, practice)
    table = next(
        name
        for name in sorted(desired)
        if not desired[name].enabled and name in live and name != "alembic_version"
    )
    _ddl(engine, f'ALTER TABLE "{practice}"."{table}" ENABLE ROW LEVEL SECURITY')
    _ddl(engine, f'ALTER TABLE "{practice}"."{table}" FORCE ROW LEVEL SECURITY')
    assert _flags(engine, practice)[table] == (True, True)

    counts = _apply(engine, practice, desired)

    assert counts.altered == 1
    assert _flags(engine, practice)[table] == (False, False)


def test_a_table_no_sql_creates_is_left_exactly_as_it_is(
    engine: Engine, practice: str, desired: dict[str, DesiredTable]
) -> None:
    _ddl(engine, f'CREATE TABLE "{practice}".added_later (id uuid PRIMARY KEY, owner text)')
    _ddl(engine, f'ALTER TABLE "{practice}".added_later ENABLE ROW LEVEL SECURITY')
    _ddl(engine, f'CREATE POLICY its_own ON "{practice}".added_later USING (owner = current_user)')
    before = (_policies(engine, practice), _flags(engine, practice))

    counts = _apply(engine, practice, desired)

    assert counts.skipped == 1
    assert counts.altered == 0
    assert (_policies(engine, practice), _flags(engine, practice)) == before


def test_a_practice_with_no_row_security_at_all_ends_up_as_the_file_says(
    engine: Engine, practice: str, from_the_file: str, desired: dict[str, DesiredTable]
) -> None:
    for table, policy in _policies(engine, practice):
        _ddl(engine, f'DROP POLICY "{policy}" ON "{practice}"."{table}"')
    for table, (enabled, _forced) in _flags(engine, practice).items():
        if enabled:
            _ddl(engine, f'ALTER TABLE "{practice}"."{table}" NO FORCE ROW LEVEL SECURITY')
            _ddl(engine, f'ALTER TABLE "{practice}"."{table}" DISABLE ROW LEVEL SECURITY')
    assert not _policies(engine, practice)
    forced = sum(table.forced for table in desired.values())

    counts = _apply(engine, practice, desired)

    assert counts.altered == forced
    assert _policies(engine, practice) == _policies(engine, from_the_file)
    assert _flags(engine, practice) == _flags(engine, from_the_file)
    assert _patients_read_by(engine, practice, _TREATING) == 2
    assert _patients_read_by(engine, practice, _COLLEAGUE) == 0

    # Some of those policies read back unlike the statement that made them.
    # The second pass has to recognise them as its own work and leave them.
    oids = _policy_oids(engine, practice)
    assert _apply(engine, practice, desired).altered == 0
    assert _policy_oids(engine, practice) == oids, "a second pass rebuilt a policy"


def test_a_schema_built_from_the_file_settles_after_one_pass(
    engine: Engine, from_the_file: str, desired: dict[str, DesiredTable]
) -> None:
    """What a practice provisioned from the template alone goes through.

    Its policies were created by the file's statements, so wherever Postgres
    stores a statement differently the first pass rebuilds that policy to
    record what it became. Nothing about the policy changes, so nothing is
    counted, and the pass after that touches nothing.
    """
    first = _policies(engine, from_the_file)
    oids = _policy_oids(engine, from_the_file)

    assert _apply(engine, from_the_file, desired).altered == 0
    assert _policies(engine, from_the_file) == first
    recorded = _policy_oids(engine, from_the_file)
    assert recorded != oids, (
        "no policy needed its stored form recorded; this test no longer covers that path"
    )

    assert _apply(engine, from_the_file, desired).altered == 0
    assert _policy_oids(engine, from_the_file) == recorded


def test_nothing_is_kept_unless_the_caller_commits(
    engine: Engine, practice: str, desired: dict[str, DesiredTable]
) -> None:
    _ddl(engine, f'DROP POLICY rls_patient_access ON "{practice}".patients')

    with OrmSession(engine) as session:
        session.execute(text(f"SET search_path = {practice}, platform, public"))
        path = session.execute(text("SHOW search_path")).scalar_one()
        counts = apply_tenant_rls(session, practice, desired)
        assert counts.altered == 1
        # It ran its statements under an empty path and put the caller's back.
        assert session.execute(text("SHOW search_path")).scalar_one() == path
        session.rollback()

    assert ("patients", "rls_patient_access") not in _policies(engine, practice)


def test_the_directory_policys_name_protects_nothing_on_another_table(
    engine: Engine, practice: str, desired: dict[str, DesiredTable]
) -> None:
    """The file puts that policy on three tables; anywhere else it is a stray."""
    _ddl(
        engine,
        f'CREATE POLICY {_DIRECTORY_POLICY} ON "{practice}".therapy_sessions USING (true)',
    )

    counts = _apply(engine, practice, desired)

    assert counts.altered == 1
    assert ("therapy_sessions", _DIRECTORY_POLICY) not in _policies(engine, practice)
    assert ("patients", _DIRECTORY_POLICY) in _policies(engine, practice)


def test_an_edit_to_a_policy_the_applier_itself_recorded_is_put_back(
    engine: Engine, desired: dict[str, DesiredTable]
) -> None:
    """The recorded hash covers what was stored, so an edit under it does not match.

    Built from the file alone, so that some policies carry the applier's own
    comment after the first pass; ``patients`` in the test above never does.
    """
    from app.db.provisioning import (  # noqa: PLC0415
        TEMPLATE_ROW_SECURITY_MARKER,
        apply_template_sql,
    )

    schema = f"practice_test_apply_edit_{uuid.uuid4().hex[:8]}"
    body, _, row_security = _TEMPLATE.read_text().partition(TEMPLATE_ROW_SECURITY_MARKER)
    apply_template_sql(engine, schema, body)
    apply_template_sql(engine, schema, row_security)
    try:
        assert _apply(engine, schema, desired).altered == 0
        with engine.connect() as conn:
            table, policy, command = conn.execute(
                text(
                    "SELECT c.relname, p.polname, p.polcmd::text FROM pg_policy p "
                    "JOIN pg_class c ON c.oid = p.polrelid "
                    "JOIN pg_namespace n ON n.oid = c.relnamespace "
                    "WHERE n.nspname = :s "
                    "AND obj_description(p.oid, 'pg_policy') LIKE :prefix "
                    "ORDER BY 1, 2 LIMIT 1"
                ),
                {"s": schema, "prefix": APPLIED_PREFIX + "%"},
            ).one()
        settled = _policies(engine, schema)

        clause = "WITH CHECK" if command == "a" else "USING"
        _ddl(engine, f'ALTER POLICY "{policy}" ON "{schema}"."{table}" {clause} (true)')
        assert _policies(engine, schema) != settled, "the damage was not done"

        assert _apply(engine, schema, desired).altered == 1
        assert _policies(engine, schema) == settled
    finally:
        _drop_schema(engine, schema)


def test_a_restrictive_policy_for_a_named_role_settles(
    engine: Engine, practice: str, desired: dict[str, DesiredTable]
) -> None:
    """The template has no such policy today; the applier must still not loop on one."""
    with engine.connect() as conn:
        role = conn.execute(text("SELECT current_user")).scalar_one()
    patients = desired["patients"]
    extended = {
        **desired,
        "patients": DesiredTable(
            enabled=patients.enabled,
            forced=patients.forced,
            policies={
                **patients.policies,
                "rls_narrow": (
                    "CREATE POLICY rls_narrow ON __TENANT_SCHEMA__.patients "
                    f"AS RESTRICTIVE FOR SELECT TO {role} USING (true);"
                ),
            },
            comments=dict(patients.comments),
        ),
    }

    assert _apply(engine, practice, extended).altered == 1
    assert _policies(engine, practice)["patients", "rls_narrow"][:2] == ("RESTRICTIVE", "SELECT")
    assert _patients_read_by(engine, practice, _TREATING) == 2

    oids = _policy_oids(engine, practice)
    assert _apply(engine, practice, extended).altered == 0
    assert _policy_oids(engine, practice) == oids


def test_a_table_the_sql_creates_that_the_schema_lacks_is_not_counted(
    engine: Engine, practice: str, desired: dict[str, DesiredTable]
) -> None:
    counts = _apply(engine, practice, {**desired, "not_migrated_yet": DesiredTable()})

    assert counts.applied == len(desired)
    assert counts.altered == 0


def test_the_differences_are_reported_without_changing_anything(
    engine: Engine, practice: str, desired: dict[str, DesiredTable]
) -> None:
    with OrmSession(engine) as session:
        assert tenant_rls_differences(session, practice, desired) == {}

    _ddl(engine, f'DROP POLICY rls_patient_access ON "{practice}".patients')
    with OrmSession(engine) as session:
        found = tenant_rls_differences(session, practice, desired)
        session.commit()

    assert list(found) == ["patients"]
    assert any("CREATE POLICY rls_patient_access" in statement for statement in found["patients"])
    assert ("patients", "rls_patient_access") not in _policies(engine, practice)


# ── Every single damage, to every policy and every table ────────────────────

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


def test_every_single_damage_to_every_table_is_found_and_undone(
    engine: Engine, desired: dict[str, DesiredTable]
) -> None:
    """Not a sample. Each policy and each table in the template, damaged each way.

    For every damage: the read-only report names that table and no other, one
    pass puts it right, and the schema is back to exactly what it was. The
    tests above do this to ``patients``; a bug that only shows on some other
    policy's shape would get past them and cannot get past this.

    Built from the file and settled first, so that a policy put back from the
    file reads the same as the one that was there before.
    """
    from app.db.provisioning import (  # noqa: PLC0415
        TEMPLATE_ROW_SECURITY_MARKER,
        apply_template_sql,
    )

    schema = f"practice_test_apply_all_{uuid.uuid4().hex[:8]}"
    body, _, row_security = _TEMPLATE.read_text().partition(TEMPLATE_ROW_SECURITY_MARKER)
    apply_template_sql(engine, schema, body)
    apply_template_sql(engine, schema, row_security)
    try:
        _apply(engine, schema, desired)
        cases = 0
        failures: list[str] = []
        # One session for the lot, and the applier's own catalog reads for the
        # comparison: seven hundred cases through ``pg_policies`` take a minute.
        with OrmSession(engine) as session:
            settled = read_live_rls(session, schema)
            for table in sorted(desired):
                for what, statements in _damages(schema, table, desired[table]):
                    cases += 1
                    cursor = session.connection().connection.cursor()
                    for statement in statements:
                        cursor.execute(statement)
                    session.commit()

                    found = tenant_rls_differences(session, schema, desired)
                    if list(found) != [table]:
                        failures.append(f"{table}: {what}: reported {sorted(found)}")
                    apply_tenant_rls(session, schema, desired)
                    session.commit()
                    after = read_live_rls(session, schema)
                    if after != settled:
                        failures.append(f"{table}: {what}: not put back")
                        # Start the next case from a known state, not from this one.
                        settled = after
        assert not failures, f"{len(failures)} of {cases} damages got through:\n" + "\n".join(
            failures[:40]
        )
        policies = sum(len(table.policies) for table in desired.values())
        assert cases == 3 * policies + 3 * len(desired)
    finally:
        _drop_schema(engine, schema)
