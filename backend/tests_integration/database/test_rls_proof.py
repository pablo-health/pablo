# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Row-level security holds: every table accounted for, and every row-scoped
table refuses every principal it does not belong to.

Two layers, both read from the database rather than from a list someone keeps:

**Coverage.** Every base table in a practice schema and in ``platform`` is
listed from ``pg_class`` and must be exactly one of:

* row-scoped: ``relrowsecurity`` and ``relforcerowsecurity`` on, at least one
  policy that applies to the app's role, and no permissive policy that applies
  to it admitting every row (a ``USING`` of ``true`` or none, or a
  ``WITH CHECK`` of ``true``). A policy applies to the app's role when it names
  ``PUBLIC`` or a role whose privileges the app's role has, either inherited
  (``pg_has_role(..., 'USAGE')``) or one ``SET ROLE`` away (``'SET'``): a
  membership granted for some other purpose must not quietly open a table;
* registered not-row-scoped (``not_row_scoped_tenant_tables()``), whose reason
  is written where it is registered;
* named in this file's exemptions, with its reason beside it.

A table in none of the three fails by name. So does an exemption naming a
table that no longer exists, and an exemption or registration whose table has
since become row-scoped: a recorded reason must not outlive the state it
describes. The practice check runs against a schema provisioned now and
against the default practice as the migrate step built it, since the two are
made by different paths (template and chain).

The "admits every row" rule reads the policy text, so it catches ``true`` and
an absent clause but not an always-true expression in other words; the
enforcement layer below is what catches those.

**Ownership.** The app's role owns the tables it reads, and Postgres does not
apply RLS to a table's owner unless the table is ``FORCE ROW LEVEL SECURITY``.
That is why row-scoped above requires ``relforcerowsecurity`` and not just
``relrowsecurity``: the owner can turn FORCE off, and the coverage check is
what notices. ``test_app_role_cannot_bypass_rls`` reports how many tables the
role owns, so the record says which of the two is holding.

**Enforcement.** As the app's own role (``NOSUPERUSER NOBYPASSRLS``, the shape
``tests_integration/conftest.py`` gives it), two practices are set up: in the
first, clinician A with patient PA and clinician B with patient PB; in the
second, clinician C with patient PC. One row owned by A / PA is synthesized in
every row-scoped table of the first practice and of ``platform``, through RLS
(``rls_row_synth``), so each one passed its table's ``WITH CHECK`` as an app
write would. Then, for every one of those rows:

* A sees it (the control: without it every "nothing visible" below is vacuous);
* clinician B, an unarmed session, patient PB, and clinician C reaching into
  the first practice with schema-qualified SQL, each SELECT nothing and
  UPDATE and DELETE zero rows. ``platform`` is shared by every practice, so
  there clinician C is simply a clinician of another practice.

Two request shapes from the 2026-11-15 changes get their own cases:

* a request that commits mid-way (``release_db_connection``) and queries again
  is still the same principal on the same practice, including when the
  request ContextVars are gone, as they are in a sync route's other worker
  thread;
* a standalone session opened while the request holds its connection comes
  from the nested pool and carries the request's principal and practice; and
  one opened where those ContextVars are empty sees nothing, rather than
  somebody else's rows.

Run: ``make test-integration``.
"""

from __future__ import annotations

import contextvars
import os
import uuid
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from tests_integration.database.patient_rows import seed_patient_row
from tests_integration.database.rls_row_synth import Owner, Synthesizer, load_catalog

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

    from sqlalchemy.engine import Engine

_db_url = os.environ.get("DATABASE_URL", "")
pytestmark = pytest.mark.skipif(
    not _db_url or os.environ.get("DATABASE_BACKEND") != "postgres",
    reason=(
        "PostgreSQL not configured. Set DATABASE_URL and "
        "DATABASE_BACKEND=postgres; testcontainers should set both."
    ),
)

# --------------------------------------------------------------------------
# Exemptions. Each is a table with row-level security off on purpose; the
# reason says what isolates it instead. Keep the reason honest: "the schema"
# means a request on another practice's search_path cannot name it, and
# nothing in the database stops schema-qualified SQL.
# --------------------------------------------------------------------------

#: Practice-schema tables with no RLS that are not in
#: ``not_row_scoped_tenant_tables()``: they carry none of the columns
#: ``enable_rls_on_schema`` keys a policy on, so it never considers them.
PRACTICE_NO_RLS: dict[str, str] = {
    "alembic_version": "the tenant chain's revision marker; no practice data",
    "allowed_emails": "vestigial per-practice sign-up allow list; no patient or clinician key",
    "ehr_prompts": "practice-level EHR prompt text keyed by ehr_system; no owner",
    "import_records": (
        "import ledger keyed by the source system's record id; names no patient or "
        "clinician (its run, import_runs, is registered not-row-scoped)"
    ),
    "payer_enrollments": (
        "the practice's enrollment with a payer; practice-level, like payers. "
        "requested_by_user_id records who asked, not who owns it"
    ),
}

_APP_SCOPED = "isolated by the application's own filter on this key, not by the database"

#: Platform tables with no RLS. The platform schema is shared by every
#: practice, so none of these is isolated by a schema either: each is reached
#: only through code that filters on the key named.
PLATFORM_NO_RLS: dict[str, str] = {
    "alembic_version_platform": "the platform chain's revision marker",
    "allowed_emails": f"sign-up allow list; {_APP_SCOPED} (email, practice_id)",
    "booking_links": f"public booking links; {_APP_SCOPED} (user_id, practice_id, slug)",
    "claim_reviews": f"claims held for review; {_APP_SCOPED} (practice_id, user_id)",
    "claim_routes": f"control number to practice routing; {_APP_SCOPED} (control_number)",
    "companion_devices": f"enrolled desktop devices; {_APP_SCOPED} (user_id)",
    "companion_practice_slugs": f"portal slug to practice; {_APP_SCOPED} (slug)",
    "diagnostic_definitions": "global reference data, the same for every practice",
    "email_tenant_mappings": f"sign-in email to practice; {_APP_SCOPED} (email)",
    "icd10_codes": "global reference data, the same for every practice",
    "launch_intents": f"one-time launch handles; {_APP_SCOPED} (intent_hash, user_id)",
    "passkey_backup_codes": f"{_APP_SCOPED} (user_id)",
    "passkey_challenges": f"{_APP_SCOPED} (challenge_hash)",
    "passkey_credentials": f"{_APP_SCOPED} (credential_id, user_id)",
    "platform_audit_logs": "append-only platform audit trail, read by operators only",
    "portal_invite_templates": f"{_APP_SCOPED} (practice_id)",
    "portal_welcome_messages": f"{_APP_SCOPED} (practice_id)",
    "practice_booking_buttons": f"{_APP_SCOPED} (practice_id)",
    "practice_domain_apexes": f"{_APP_SCOPED} (practice_id, apex)",
    "practice_domains": f"{_APP_SCOPED} (practice_id, domain)",
    "practice_email_senders": f"{_APP_SCOPED} (practice_id)",
    "practice_portal_settings": f"{_APP_SCOPED} (practice_id)",
    "practice_site_versions": f"{_APP_SCOPED} (practice_id)",
    "practice_sites": f"{_APP_SCOPED} (practice_id)",
    "practices": "the practice registry; resolves a request to its schema",
    "processed_payment_events": "payment webhook dedupe keyed by the vendor's event id",
    "setup_tokens": f"one-time setup links; {_APP_SCOPED} (token)",
    "system_config": "deployment-wide settings",
    "user_identities": f"sign-in provider to user; {_APP_SCOPED} (provider, subject_id)",
    "user_preferences": f"{_APP_SCOPED} (user_id)",
    "users": f"platform identity; {_APP_SCOPED} (id)",
}

APP_ROLE = "pablo"


@dataclass(frozen=True)
class TableState:
    name: str
    rls: bool
    forced: bool
    app_policies: int
    # permissive policies that apply to the app role and admit every row
    open_policies: tuple[str, ...] = ()

    @property
    def row_scoped(self) -> bool:
        return self.rls and self.forced and self.app_policies > 0 and not self.open_policies


#: A policy applies to the app role: it names PUBLIC, or a role the app role
#: inherits (USAGE) or can SET ROLE to (SET).
_APPLIES_TO_APP = (
    "(p.polroles = '{0}' OR EXISTS ("
    "   SELECT 1 FROM pg_roles r WHERE r.oid = ANY (p.polroles) "
    "   AND (pg_has_role(:role, r.oid, 'USAGE') OR pg_has_role(:role, r.oid, 'SET'))))"
)

#: A permissive policy that admits every row for the commands it covers. An
#: INSERT policy has no USING; any other has its USING checked, and its WITH
#: CHECK falls back to the USING when absent.
_ADMITS_EVERY_ROW = (
    "p.polpermissive AND ("
    " (p.polcmd <> 'a' AND (p.polqual IS NULL"
    "   OR pg_get_expr(p.polqual, p.polrelid) = 'true'))"
    " OR (p.polcmd = 'a' AND p.polwithcheck IS NULL)"
    " OR (p.polcmd IN ('a', 'w', '*')"
    "   AND pg_get_expr(p.polwithcheck, p.polrelid) = 'true'))"
)


def _table_states(conn: Any, schema: str) -> dict[str, TableState]:
    """Every base table of ``schema`` with its RLS flags and policies the app role gets."""
    rows = conn.execute(
        # the interpolated parts are this module's constants
        text(
            "SELECT c.relname, c.relrowsecurity, c.relforcerowsecurity, "  # noqa: S608
            f"  (SELECT count(*) FROM pg_policy p WHERE p.polrelid = c.oid AND {_APPLIES_TO_APP}), "
            "  array(SELECT p.polname || ' (' || p.polroles::regrole[]::text || ')' "
            "        FROM pg_policy p WHERE p.polrelid = c.oid "
            f"       AND {_APPLIES_TO_APP} AND {_ADMITS_EVERY_ROW} ORDER BY 1) "
            "FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
            "WHERE n.nspname = :s AND c.relkind IN ('r', 'p') ORDER BY 1"
        ),
        {"s": schema, "role": APP_ROLE},
    ).all()
    return {r[0]: TableState(r[0], r[1], r[2], r[3], tuple(r[4])) for r in rows}


def _classify(
    states: dict[str, TableState], registered: set[str], exempt: dict[str, str]
) -> tuple[list[str], list[str], list[str], list[str]]:
    """Split tables into (row-scoped, registered, exempt, unaccounted).

    Unaccounted includes a registered or exempt table that has become
    row-scoped: its recorded reason no longer describes it, so it is stale,
    and a stale reason is how the next change to that table goes unread.
    """
    scoped, reg, ex, bad = [], [], [], []
    for name, st in sorted(states.items()):
        listed = "registered" if name in registered else "exempt" if name in exempt else None
        if st.row_scoped and listed:
            bad.append(f"{name} is row-scoped now; its {listed} entry is stale, remove it")
        elif st.row_scoped:
            scoped.append(name)
        elif listed == "registered" and not st.rls:
            reg.append(name)
        elif listed == "exempt" and not st.rls:
            ex.append(name)
        elif st.open_policies:
            bad.append(f"{name}: policies admit every row: {', '.join(st.open_policies)}")
        else:
            bad.append(f"{name} (rls={st.rls} forced={st.forced} app_policies={st.app_policies})")
    return scoped, reg, ex, bad


# --------------------------------------------------------------------------
# Fixtures
# --------------------------------------------------------------------------


@pytest.fixture(scope="module")
def fresh_practice(engine: Engine) -> Iterator[str]:
    from app.db.provisioning import create_practice_schema  # noqa: PLC0415

    schema = f"practice_rls_proof_{uuid.uuid4().hex[:8]}"
    create_practice_schema(engine, schema)
    yield schema
    with engine.connect() as conn:
        conn.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        conn.commit()


@dataclass(frozen=True)
class Target:
    """One row A owns, and how to reach it."""

    schema: str
    table: str
    pk: dict[str, Any]
    # a SET clause that reads no column, for the blind-write probe
    blind_assign: str

    @property
    def qualified(self) -> str:
        return f'"{self.schema}"."{self.table}"'


@dataclass
class World:
    p1: str
    p2: str
    a: str
    b: str
    c: str
    pa: str
    pb: str
    pc: str
    # one row A owns in every row-scoped table of p1 and of platform
    targets: list[Target]


def _in_fresh_context[T](fn: Callable[[], T]) -> T:
    """Run ``fn`` with the tenant and principal ContextVars at their defaults.

    Each principal below is its own request, so none may inherit another's
    ContextVars; an empty context is what a new request starts from.
    """
    return contextvars.Context().run(fn)


def _session_as(
    engine: Engine, schema: str, *, user: str | None = None, patient: str | None = None
) -> Session:
    from app.db import (  # noqa: PLC0415
        arm_current_patient_id,
        arm_current_user_id,
        set_tenant_schema,
    )

    session = Session(engine)
    set_tenant_schema(session, schema)
    if user is not None:
        arm_current_user_id(session, user)
    if patient is not None:
        arm_current_patient_id(session, patient)
    return session


def _seed_owned(
    session: Session, schema: str, owner: Owner, preseeded: dict[str, dict[str, Any]]
) -> tuple[list[Target], list[tuple[str, dict[str, Any]]]]:
    """Synthesize A's row in every row-scoped table of ``schema``.

    Returns the targets and every row written, in insertion order (parents
    first), so a caller can remove what it wrote.
    """
    catalog = load_catalog(session, schema)
    synth = Synthesizer(session, schema, owner, catalog)
    synth.rows.update(preseeded)
    forced = _row_scoped(session, schema)
    failed: list[str] = []
    for table in forced:
        before = dict(synth.rows)
        savepoint = session.begin_nested()
        try:
            synth.row_for(table)
            savepoint.commit()
        except Exception as exc:
            savepoint.rollback()
            synth.rows = before
            failed.append(f"{schema}.{table}: {str(exc).splitlines()[0]}")
    assert not failed, "could not synthesize an owned row in:\n  " + "\n  ".join(failed)
    targets = [
        Target(
            schema,
            t,
            {k: synth.rows[t][k] for k in catalog[t].pk},
            _blind_assignment(catalog[t]),
        )
        for t in forced
    ]
    written = [
        (t, {k: row[k] for k in catalog[t].pk})
        for t, row in synth.rows.items()
        if t not in preseeded
    ]
    return targets, written


@pytest.fixture(scope="module")
def world(engine: Engine) -> Iterator[World]:
    from app.db import PLATFORM_SCHEMA  # noqa: PLC0415
    from app.db.provisioning import create_practice_schema  # noqa: PLC0415

    p1 = f"practice_rls_one_{uuid.uuid4().hex[:8]}"
    p2 = f"practice_rls_two_{uuid.uuid4().hex[:8]}"
    create_practice_schema(engine, p1)
    create_practice_schema(engine, p2)
    a, b, c = (str(uuid.uuid4()) for _ in range(3))

    def seed() -> tuple[str, str, str, list[Target], list[tuple[str, dict[str, Any]]]]:
        with _session_as(engine, p1, user=a) as s:
            pa = seed_patient_row(s, a)
            owner = Owner(user_id=a, patient_id=pa)
            # seed_patient_row made the patient and A's grant on it already.
            preseeded: dict[str, dict[str, Any]] = {}
            for parent, key in (("patients", "id"), ("patient_clinicians", "patient_id")):
                existing = f'SELECT * FROM "{p1}"."{parent}" WHERE "{key}" = :p'  # noqa: S608
                preseeded[parent] = dict(s.execute(text(existing), {"p": pa}).mappings().one())
            targets, _ = _seed_owned(s, p1, owner, preseeded)
            platform_targets, platform_rows = _seed_owned(s, PLATFORM_SCHEMA, owner, {})
            s.commit()
        with _session_as(engine, p1, user=b) as s:
            pb = seed_patient_row(s, b)
            s.commit()
        with _session_as(engine, p2, user=c) as s:
            pc = seed_patient_row(s, c)
            s.commit()
        return pa, pb, pc, targets + platform_targets, platform_rows

    platform_rows: list[tuple[str, dict[str, Any]]] = []

    def unseed() -> None:
        # platform is shared and outlives this module: remove A's rows, as A,
        # children first.
        with _session_as(engine, p1, user=a) as s:
            for table, pk in reversed(platform_rows):
                where, params = _where(pk)
                s.execute(text(f'DELETE FROM "{PLATFORM_SCHEMA}"."{table}" WHERE {where}'), params)  # noqa: S608
            s.commit()

    try:
        pa, pb, pc, targets, platform_rows = _in_fresh_context(seed)
        yield World(p1, p2, a, b, c, pa, pb, pc, targets)
        _in_fresh_context(unseed)
    finally:
        # A failed seed rolls its session back, so platform needs nothing then;
        # the two practice schemas were created before it and always go.
        with engine.connect() as conn:
            conn.execute(text(f'DROP SCHEMA IF EXISTS "{p1}" CASCADE'))
            conn.execute(text(f'DROP SCHEMA IF EXISTS "{p2}" CASCADE'))
            conn.commit()


def _blind_assignment(table: Any) -> str:
    """``col = DEFAULT`` (or ``= NULL``) for some column: a SET that reads nothing."""
    for col in table.columns:
        if col.has_default and col.name not in table.pk and not col.is_identity:
            return f'"{col.name}" = DEFAULT'
    for col in table.columns:
        if col.nullable:
            return f'"{col.name}" = NULL'
    col = table.columns[0]
    return f'"{col.name}" = DEFAULT'


def _row_scoped(session: Session, schema: str) -> list[str]:
    return list(
        session.execute(
            text(
                "SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
                "WHERE n.nspname = :s AND c.relkind = 'r' AND c.relrowsecurity "
                "AND c.relforcerowsecurity ORDER BY 1"
            ),
            {"s": schema},
        ).scalars()
    )


def _where(pk: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    clauses = [f'"{col}" = :k{i}' for i, col in enumerate(pk)]
    return " AND ".join(clauses), {f"k{i}": v for i, v in enumerate(pk.values())}


def _visible(session: Session, qualified: str, pk: dict[str, Any]) -> int:
    where, params = _where(pk)
    sql = f"SELECT count(*) FROM {qualified} WHERE {where}"  # noqa: S608
    return int(session.execute(text(sql), params).scalar_one())


def _attempt(session: Session, qualified: str, pk: dict[str, Any]) -> tuple[int, int, int]:
    """(rows visible, rows updated, rows deleted) for one row, rolled back after."""
    where, params = _where(pk)
    first = next(iter(pk))
    seen = _visible(session, qualified, pk)
    update = f'UPDATE {qualified} SET "{first}" = "{first}" WHERE {where}'  # noqa: S608
    delete = f"DELETE FROM {qualified} WHERE {where}"  # noqa: S608
    updated = session.execute(text(update), params).rowcount  # type: ignore[attr-defined]
    deleted = session.execute(text(delete), params).rowcount  # type: ignore[attr-defined]
    session.rollback()
    return seen, updated, deleted


def _blind_writes(session: Session, qualified: str, assign: str) -> str | None:
    """Whether a write that reads no column reaches rows a SELECT cannot see.

    An UPDATE or DELETE with a WHERE clause must also pass the table's SELECT
    policies, so a too-wide UPDATE or DELETE policy hides behind a correct
    SELECT one. A statement with no WHERE that reads nothing (``SET col =
    DEFAULT``, a bare ``DELETE``) is checked against the write policy alone.
    It reaches every row the principal may write, its own included, so the
    test is the count: never more rows than the principal can SELECT. A
    trigger or constraint firing on a row the principal cannot see means the
    write reached it, and is reported the same way.
    """
    visible = session.execute(text(f"SELECT count(*) FROM {qualified}")).scalar_one()  # noqa: S608
    found: list[str] = []
    for verb, sql in (
        ("blind update", f"UPDATE {qualified} SET {assign}"),  # noqa: S608
        ("blind delete", f"DELETE FROM {qualified}"),  # noqa: S608
    ):
        savepoint = session.begin_nested()
        try:
            reached = session.execute(text(sql)).rowcount  # type: ignore[attr-defined]
        except Exception as exc:
            savepoint.rollback()
            if visible == 0:
                found.append(f"{verb} raised on rows it cannot see: {str(exc).splitlines()[0]}")
            continue
        savepoint.rollback()
        if reached > visible:
            found.append(f"{verb} reached {reached} rows, select sees {visible}")
    session.rollback()
    return "; ".join(found) or None


# --------------------------------------------------------------------------
# Coverage
# --------------------------------------------------------------------------


class TestCoverage:
    def test_app_role_cannot_bypass_rls(self, engine: Engine) -> None:
        with engine.connect() as conn:
            row = conn.execute(
                text(
                    "SELECT current_user, rolsuper, rolbypassrls FROM pg_roles "
                    "WHERE rolname = current_user"
                )
            ).one()
        assert row == (APP_ROLE, False, False), f"app role posture {row!r}"
        # Not an assertion: owning the tables is the ordinary shape. Reported
        # so a run against a deployed database records which of FORCE and
        # non-ownership is what holds RLS on the app role.
        with engine.connect() as conn:
            owned, total = conn.execute(
                text(
                    "SELECT count(*) FILTER (WHERE c.relowner = (SELECT oid FROM pg_roles "
                    "WHERE rolname = current_user)), count(*) "
                    "FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
                    "WHERE c.relkind IN ('r', 'p') AND c.relrowsecurity"
                )
            ).one()
        print(
            f"\n[rls-coverage] the app role owns {owned} of the {total} tables with RLS on; "
            "on those RLS holds only through FORCE ROW LEVEL SECURITY"
        )

    @pytest.mark.parametrize("which", ["fresh", "default"])
    def test_every_practice_table_is_accounted_for(
        self, engine: Engine, fresh_practice: str, which: str
    ) -> None:
        from app.db import (  # noqa: PLC0415
            DEFAULT_PRACTICE_OWN_SCHEMA,
            not_row_scoped_tenant_tables,
        )

        schema = fresh_practice if which == "fresh" else DEFAULT_PRACTICE_OWN_SCHEMA
        registered = not_row_scoped_tenant_tables()
        with engine.connect() as conn:
            states = _table_states(conn, schema)
        assert states, f"{schema} has no tables; nothing was checked"
        scoped, reg, ex, bad = _classify(states, registered, PRACTICE_NO_RLS)
        print(
            f"\n[rls-coverage] {schema}: {len(states)} tables = {len(scoped)} row-scoped "
            f"+ {len(reg)} registered not-row-scoped + {len(ex)} exempt"
        )
        print(f"[rls-coverage]   registered: {', '.join(reg)}")
        for name in ex:
            print(f"[rls-coverage]   exempt {name}: {PRACTICE_NO_RLS[name]}")
        assert not bad, "practice tables with no RLS and no exemption:\n  " + "\n  ".join(bad)
        stale = sorted(set(PRACTICE_NO_RLS) - set(states))
        assert not stale, f"exemptions for tables that no longer exist: {stale}"

    def test_every_platform_table_is_accounted_for(self, engine: Engine) -> None:
        from app.db import PLATFORM_SCHEMA  # noqa: PLC0415

        with engine.connect() as conn:
            states = _table_states(conn, PLATFORM_SCHEMA)
        assert states, "platform has no tables; nothing was checked"
        scoped, _reg, ex, bad = _classify(states, set(), PLATFORM_NO_RLS)
        print(
            f"\n[rls-coverage] platform: {len(states)} tables = {len(scoped)} row-scoped "
            f"+ {len(ex)} exempt"
        )
        for name in ex:
            print(f"[rls-coverage]   exempt {name}: {PLATFORM_NO_RLS[name]}")
        assert not bad, "platform tables with no RLS and no exemption:\n  " + "\n  ".join(bad)
        stale = sorted(set(PLATFORM_NO_RLS) - set(states))
        assert not stale, f"exemptions for tables that no longer exist: {stale}"


# --------------------------------------------------------------------------
# Enforcement
# --------------------------------------------------------------------------


class TestEnforcement:
    def test_every_row_scoped_table_was_seeded(self, engine: Engine, world: World) -> None:
        from app.db import PLATFORM_SCHEMA  # noqa: PLC0415

        counts = []
        for schema in (world.p1, PLATFORM_SCHEMA):
            with engine.connect() as conn:
                states = _table_states(conn, schema)
            scoped = {n for n, st in states.items() if st.rls and st.forced}
            seeded = {t.table for t in world.targets if t.schema == schema}
            missing = sorted(scoped - seeded)
            assert scoped == seeded, f"{schema}: row-scoped but not probed: {missing}"
            assert seeded, f"{schema}: no row-scoped table was seeded; nothing was probed"
            where = "platform" if schema == PLATFORM_SCHEMA else "practice"
            counts.append(f"{len(seeded)} in {where}")
        print(f"\n[rls-enforcement] row-scoped tables seeded and probed: {' + '.join(counts)}")

    def test_owner_sees_every_seeded_row(self, engine: Engine, world: World) -> None:
        """The control. A row its owner cannot see proves nothing when others cannot either."""

        def probe() -> list[str]:
            with _session_as(engine, world.p1, user=world.a) as s:
                return [t.qualified for t in world.targets if _visible(s, t.qualified, t.pk) != 1]

        unseen = _in_fresh_context(probe)
        assert not unseen, f"the owning clinician cannot see their own row in: {unseen}"

    @pytest.mark.parametrize(
        "principal",
        ["other_clinician", "unarmed", "other_patient", "other_practice_qualified"],
    )
    def test_no_other_principal_reaches_a_row(
        self, engine: Engine, world: World, principal: str
    ) -> None:
        def open_session() -> Session:
            if principal == "other_clinician":
                return _session_as(engine, world.p1, user=world.b)
            if principal == "unarmed":
                return _session_as(engine, world.p1)
            if principal == "other_patient":
                return _session_as(engine, world.p1, patient=world.pb)
            return _session_as(engine, world.p2, user=world.c)

        def probe() -> list[str]:
            leaks: list[str] = []
            with open_session() as s:
                for t in world.targets:
                    seen, updated, deleted = _attempt(s, t.qualified, t.pk)
                    if (seen, updated, deleted) != (0, 0, 0):
                        leaks.append(
                            f"{t.qualified}: select={seen} update={updated} delete={deleted}"
                        )
                    blind = _blind_writes(s, t.qualified, t.blind_assign)
                    if blind:
                        leaks.append(f"{t.qualified}: {blind}")
            return leaks

        leaks = _in_fresh_context(probe)
        assert not leaks, f"{principal} reached rows it does not own:\n  " + "\n  ".join(leaks)
        n = len(world.targets)
        print(f"\n[rls-enforcement] {principal}: 0 rows over {n} tables x 5 probes")


# --------------------------------------------------------------------------
# The two request shapes from 2026-11-15
# --------------------------------------------------------------------------


def _visible_patients(session: Session) -> set[str]:
    return {str(r) for r in session.execute(text("SELECT id FROM patients")).scalars()}


def _guc_and_schema(session: Session) -> tuple[str, str]:
    row = session.execute(
        text("SELECT current_setting('app.current_user_id', true), current_schema()")
    ).one()
    return row[0], row[1]


class TestMidRequestCommit:
    """``release_db_connection`` commits; the next query must be the same principal."""

    @pytest.mark.parametrize("context_vars_survive", [True, False])
    def test_release_then_query_is_the_same_principal(
        self, world: World, context_vars_survive: bool
    ) -> None:
        from app import db  # noqa: PLC0415

        def request() -> list[tuple[str, str, set[str]]]:
            session = db.get_session_factory()()
            binding = db.publish_request_session(session)
            try:
                db.set_tenant_schema(session, world.p1)
                db.arm_current_user_id(session, world.b)
                seen = [(*_guc_and_schema(session), _visible_patients(session))]
                db.release_db_connection()
                assert not session.in_transaction()
                if not context_vars_survive:
                    # A sync route's endpoint runs in another worker thread than
                    # the dependency that armed it: the ContextVars are gone
                    # there, and only session.info carries the principal.
                    db._current_user_id.set(None)
                    db._current_tenant_schema.set(None)
                seen.append((*_guc_and_schema(session), _visible_patients(session)))
                db.release_db_connection()
                seen.append((*_guc_and_schema(session), _visible_patients(session)))
                return seen
            finally:
                session.rollback()
                session.close()
                db.restore_request_session(binding)

        snapshots = _in_fresh_context(request)
        expected = (world.b, world.p1, {world.pb})
        for i, snap in enumerate(snapshots):
            assert snap == expected, f"after {i} release(s): {snap!r}, expected {expected!r}"


class TestNestedPool:
    """A standalone session opened mid-request carries the request's principal and practice."""

    def test_standalone_session_while_holding_carries_principal(self, world: World) -> None:
        from app import db  # noqa: PLC0415

        def request() -> tuple[bool, tuple[str, str], set[str]]:
            session = db.get_session_factory()()
            binding = db.publish_request_session(session)
            try:
                db.set_tenant_schema(session, world.p1)
                db.arm_current_user_id(session, world.b)
                _visible_patients(session)  # the request now holds a connection
                assert session.in_transaction()
                nested = db.create_standalone_session()
                try:
                    from_nested = nested.get_bind() is db.get_nested_engine()
                    return from_nested, _guc_and_schema(nested), _visible_patients(nested)
                finally:
                    nested.rollback()
                    nested.close()
            finally:
                session.rollback()
                session.close()
                db.restore_request_session(binding)

        from_nested, (guc, schema), visible = _in_fresh_context(request)

        if db.get_nested_engine() is not db.get_engine():
            assert from_nested, (
                "a standalone session opened while holding did not use the nested pool"
            )
        assert (guc, schema) == (world.b, world.p1)
        assert visible == {world.pb}, f"nested session saw {visible!r}"

    def test_standalone_session_without_context_sees_nothing(self, world: World) -> None:
        """Where the ContextVars are empty (a sync route's other thread), it fails closed."""
        from app import db  # noqa: PLC0415

        def request() -> tuple[str | None, set[str]]:
            session = db.get_session_factory()()
            binding = db.publish_request_session(session)
            try:
                db.set_tenant_schema(session, world.p1)
                db.arm_current_user_id(session, world.b)
                _visible_patients(session)
                db._current_user_id.set(None)
                nested = db.create_standalone_session(world.p1)
                try:
                    guc = nested.execute(
                        text("SELECT current_setting('app.current_user_id', true)")
                    ).scalar_one()
                    return guc, _visible_patients(nested)
                finally:
                    nested.rollback()
                    nested.close()
            finally:
                session.rollback()
                session.close()
                db.restore_request_session(binding)

        guc, visible = _in_fresh_context(request)
        assert not guc, f"a context-free standalone session carried principal {guc!r}"
        assert visible == set(), f"a context-free standalone session saw {visible!r}"
