# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Remembered answers belong to the practice, proven against real Postgres.

``e5b7c2a9d4f1`` gives ``patient_source_mappings`` a scope, a unique key over
scoped rows, and a policy that shows a scoped row to any armed clinician and
a row from before (plain identifier, no scope) to its owner alone; gives
``appointments`` one live row per outside event; and adds the lookup a second
follower links through. Rows from before are adopted by the app, on the
first read, digested and re-scoped, the newer answer standing.

Everything here runs as the ``pablo`` role the integration conftest creates
``NOSUPERUSER NOBYPASSRLS``, on schemas built by the real
``create_practice_schema`` and migrated by the real chain. Every
invisibility assertion has a visibility control beside it.

Run: ``make test-integration``.
"""

from __future__ import annotations

import base64
import os
import threading
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from app.calendar_providers.source_identity import (
    GOOGLE_CALENDAR_SOURCE,
    calendar_source_identifier,
    ical_source,
)
from app.db import PLATFORM_SCHEMA, _current_tenant_schema, arm_current_user_id, set_tenant_schema
from app.db.migrate_tenants import TenantStatus, _alembic_config_for, upgrade_tenant_schema
from app.db.provisioning import create_practice_schema
from app.patients.identifiers import PRACTICE_SCOPE, calendar_scope, identifier_digest
from app.patients.matching import MatchContext, PatientHint, match_patient
from app.repositories.external_calendar_event import ANSWER_CLIENT, ExternalCalendarEvent
from app.repositories.patient_source_mapping import PatientSourceMapping
from app.repositories.postgres.appointment import PostgresAppointmentRepository
from app.repositories.postgres.external_calendar_event import (
    PostgresExternalCalendarEventRepository,
)
from app.repositories.postgres.patient import PostgresPatientRepository
from app.repositories.postgres.patient_source_mapping import (
    PostgresPatientSourceMappingRepository,
)
from app.scheduling_engine.exceptions import OutsideEventAlreadyBookedError
from app.scheduling_engine.models.appointment import Appointment, AppointmentStatus
from app.services.outside_sessions import OutsideSessions
from app.settings import get_settings
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

if TYPE_CHECKING:
    from collections.abc import Iterator

    from sqlalchemy.engine import Engine

_db_url = os.environ.get("DATABASE_URL", "")
pytestmark = pytest.mark.skipif(
    not _db_url or os.environ.get("DATABASE_BACKEND") != "postgres",
    reason="PostgreSQL not configured. Set DATABASE_URL and DATABASE_BACKEND=postgres.",
)

_PARENT = "d6a2e9f4b1c8"
_UNDER_TEST = "e5b7c2a9d4f1"
_A = "11111111-2222-3333-4444-555555555555"
_B = "66666666-7777-8888-9999-000000000000"
MAIN = "shared@group.calendar.google.test"
SH = "sessions_health"
_ROLE = "pablo_practice_directory"


@pytest.fixture(scope="module")
def engine() -> Iterator[Engine]:
    backend_dir = Path(__file__).resolve().parents[2]
    cfg = Config(str(backend_dir / "alembic.ini"))
    cfg.set_main_option("script_location", str(backend_dir / "alembic"))
    command.upgrade(cfg, "head")
    eng = create_engine(_db_url, pool_pre_ping=True)
    yield eng
    eng.dispose()


def _new_key() -> str:
    return base64.b64encode(os.urandom(32)).decode()


@pytest.fixture(autouse=True)
def _calendar_key(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("GOOGLE_CALENDAR_ENCRYPTION_KEY", _new_key())
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _set_rls(conn, schema: str, table: str, *, on: bool) -> None:
    if on:
        conn.execute(text(f"ALTER TABLE {schema}.{table} ENABLE ROW LEVEL SECURITY"))
        conn.execute(text(f"ALTER TABLE {schema}.{table} FORCE ROW LEVEL SECURITY"))
    else:
        conn.execute(text(f"ALTER TABLE {schema}.{table} NO FORCE ROW LEVEL SECURITY"))
        conn.execute(text(f"ALTER TABLE {schema}.{table} DISABLE ROW LEVEL SECURITY"))


def _past_rls(engine: Engine, schema: str, table: str, sql: str, **params):
    """Read or write past the row policy — the suite's role is NOBYPASSRLS.

    Turning the policy off is an ALTER TABLE, so it waits for every open
    transaction that has touched the table. A test that left one open would
    wait on itself forever; the lock timeout makes that a failure instead.
    """
    with engine.begin() as conn:
        conn.execute(text("SET LOCAL lock_timeout = '10s'"))
        conn.execute(text(f"SET search_path = {schema}, {PLATFORM_SCHEMA}, public"))
        _set_rls(conn, schema, table, on=False)
        try:
            return (
                conn.execute(text(sql), params).all()
                if sql.lstrip().upper().startswith("SELECT")
                else conn.execute(text(sql), params)
            )
        finally:
            _set_rls(conn, schema, table, on=True)


def _columns(engine: Engine, schema: str, table: str) -> set[str]:
    with engine.connect() as conn:
        return {
            row[0]
            for row in conn.execute(
                text(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_schema = :schema AND table_name = :table"
                ),
                {"schema": schema, "table": table},
            )
        }


def _indexes(engine: Engine, schema: str, table: str) -> set[str]:
    with engine.connect() as conn:
        return set(
            conn.execute(
                text("SELECT indexname FROM pg_indexes WHERE schemaname = :s AND tablename = :t"),
                {"s": schema, "t": table},
            ).scalars()
        )


def _policies(engine: Engine, schema: str, table: str) -> set[str]:
    with engine.connect() as conn:
        return set(
            conn.execute(
                text("SELECT policyname FROM pg_policies WHERE schemaname = :s AND tablename = :t"),
                {"s": schema, "t": table},
            ).scalars()
        )


def _function_owner(engine: Engine, schema: str) -> str | None:
    with engine.connect() as conn:
        owner: str | None = conn.execute(
            text("SELECT pg_get_userbyid(proowner) FROM pg_proc WHERE oid = to_regprocedure(:fn)"),
            {"fn": f"{schema}.practice_outside_appointment(text, text, text)"},
        ).scalar()
    return owner


def _downgrade(engine: Engine, schema: str, target: str) -> None:
    with engine.begin() as conn:
        conn.execute(text(f"SET search_path = {schema}, {PLATFORM_SCHEMA}, public"))
        cfg = _alembic_config_for(schema)
        cfg.attributes["connection"] = conn
        cfg.attributes["version_table_schema"] = schema
        command.downgrade(cfg, target)


def _chart(engine: Engine, schema: str, first: str, last: str, *clinicians: str) -> str:
    """A chart every named clinician holds a grant on, written past the policy."""
    patient_id = str(uuid.uuid4())
    with engine.begin() as conn:
        conn.execute(text(f"SET search_path = {schema}, {PLATFORM_SCHEMA}, public"))
        for table in ("patients", "patient_clinicians"):
            _set_rls(conn, schema, table, on=False)
        conn.execute(
            text(
                "INSERT INTO patients (id, first_name, last_name, first_name_lower, "
                "last_name_lower, status, session_count, created_at, updated_at) "
                "VALUES (:pid, :first, :last, lower(:first), lower(:last), 'active', 0, "
                "now(), now())"
            ),
            {"pid": patient_id, "first": first, "last": last},
        )
        for clinician in clinicians:
            conn.execute(
                text(
                    "INSERT INTO patient_clinicians (patient_id, user_id, granted_by) "
                    "VALUES (:pid, :u, :u)"
                ),
                {"pid": patient_id, "u": clinician},
            )
        for table in ("patients", "patient_clinicians"):
            _set_rls(conn, schema, table, on=True)
    return patient_id


def _legacy_answer(  # noqa: PLR0913 — one old row, every column named
    engine: Engine,
    schema: str,
    user_id: str,
    source: str,
    identifier: str,
    patient_id: str | None,
    *,
    days_ago: int = 30,
) -> str:
    """A row as the table held it before this revision: one clinician's, in plain text."""
    doc_id = f"{user_id}_{source}_{identifier}"
    _past_rls(
        engine,
        schema,
        "patient_source_mappings",
        "INSERT INTO patient_source_mappings (doc_id, user_id, source, source_identifier, "
        "answer, patient_id, created_at) VALUES (:doc, :uid, :source, :ident, :answer, :pid, :ts)",
        doc=doc_id,
        uid=user_id,
        source=source,
        ident=identifier,
        answer="client" if patient_id else "not_a_client",
        pid=patient_id,
        ts=datetime.now(UTC) - timedelta(days=days_ago),
    )
    return doc_id


def _all_answers(engine: Engine, schema: str) -> list[tuple]:
    return [
        tuple(r)
        for r in _past_rls(
            engine,
            schema,
            "patient_source_mappings",
            "SELECT doc_id, scope, source, source_identifier, patient_id::text, answer, "
            "user_id::text, answered_by_user_id::text, session_clinician_user_id::text "
            "FROM patient_source_mappings ORDER BY doc_id",
        )
    ]


def _answers_at_parent(engine: Engine, schema: str) -> list[tuple]:
    """The answers as the parent revision can read them: no scope column yet.

    Shaped like the first seven columns of :func:`_all_answers`, with the
    scope as NULL, so the two compare directly.
    """
    return [
        tuple(r)
        for r in _past_rls(
            engine,
            schema,
            "patient_source_mappings",
            "SELECT doc_id, NULL, source, source_identifier, patient_id::text, answer, "
            "user_id::text FROM patient_source_mappings ORDER BY doc_id",
        )
    ]


def _session(engine: Engine, schema: str, user_id: str) -> Session:
    sess = Session(bind=engine)
    set_tenant_schema(sess, schema)
    arm_current_user_id(sess, user_id)
    return sess


@pytest.fixture
def opened() -> Iterator[list[Session]]:
    """Sessions opened by a test, each armed as one clinician; all closed afterwards."""
    sessions: list[Session] = []
    yield sessions
    for sess in sessions:
        sess.rollback()
        sess.close()
    _current_tenant_schema.set(None)


def _outside(sess: Session, main_calendar_id: str | None = MAIN) -> OutsideSessions:
    return OutsideSessions(
        PostgresExternalCalendarEventRepository(sess),
        PostgresAppointmentRepository(sess),
        PostgresPatientRepository(sess),
        PostgresPatientSourceMappingRepository(sess),
        main_calendar_id=main_calendar_id,
    )


def _appointment(
    user_id: str, event_id: str, calendar_id: str | None, patient_id: str
) -> Appointment:
    start = datetime(2099, 1, 5, 15, tzinfo=UTC)
    return Appointment(
        id=str(uuid.uuid4()),
        user_id=user_id,
        patient_id=patient_id,
        title="Session",
        start_at=start,
        end_at=start + timedelta(minutes=50),
        duration_minutes=50,
        status=AppointmentStatus.CONFIRMED,
        session_type="individual",
        outside_source=GOOGLE_CALENDAR_SOURCE if calendar_id else ical_source(SH),
        outside_event_id=event_id,
        outside_calendar_id=calendar_id,
        created_at=datetime.now(UTC),
    )


# --- The revision ---------------------------------------------------------------------


def test_parent_revision_is_still_the_one_this_fixture_assumes() -> None:
    script = ScriptDirectory.from_config(_alembic_config_for("practice"))
    assert script.get_revision(_UNDER_TEST).down_revision == _PARENT


@pytest.fixture
def tenant_at_parent(engine: Engine) -> Iterator[dict[str, str]]:
    """A tenant at ``_PARENT``: two old answers, and three live rows for one event.

    Of the three appointments following event ``e1`` on the main calendar for
    clinician A, one is cancelled (left alone), and two are live: the older
    ``kept`` and the newer ``dup``, whose open row points at ``dup``.

    ``dup`` is an hour later, as when the event moved and was booked again:
    one clinician cannot have two live appointments at the same start
    (``uq_appointments_user_start_active``), so real duplicates differ in time.
    """
    schema = f"practice_test_owned_{uuid.uuid4().hex[:8]}"
    create_practice_schema(engine, schema)
    _downgrade(engine, schema, _PARENT)
    patient_id = _chart(engine, schema, "Jane", "Smith", _A, _B)
    seeded = {"schema": schema, "patient": patient_id}
    seeded["feed_doc"] = _legacy_answer(engine, schema, _A, SH, "SH00001", patient_id)
    seeded["series_doc"] = _legacy_answer(
        engine, schema, _A, GOOGLE_CALENDAR_SOURCE, "series:wk", patient_id
    )

    ids = {"cancelled": str(uuid.uuid4()), "kept": str(uuid.uuid4()), "dup": str(uuid.uuid4())}
    with engine.begin() as conn:
        conn.execute(text(f"SET search_path = {schema}, {PLATFORM_SCHEMA}, public"))
        for table in ("appointments", "external_calendar_events"):
            _set_rls(conn, schema, table, on=False)
        for name, status, minutes_ago, hour in (
            ("cancelled", "cancelled", 30, 15),
            ("kept", "confirmed", 20, 15),
            ("dup", "confirmed", 10, 16),
        ):
            conn.execute(
                text(
                    # The three flags default in the ORM, not in the table, so
                    # a row written in SQL has to say them.
                    "INSERT INTO appointments (id, user_id, patient_id, title, start_at, end_at, "
                    "duration_minutes, status, session_type, outside_source, outside_event_id, "
                    "outside_calendar_id, is_exception, reminder_24h_sent, reminder_1h_sent, "
                    "created_at, updated_at) VALUES (:id, :u, :p, 'Session', "
                    ":start, :end, 50, :status, 'individual', "
                    "'google_calendar', 'e1', :cal, false, false, false, "
                    "now() - make_interval(mins => :ago), now())"
                ),
                {
                    "id": ids[name],
                    "u": _A,
                    "p": patient_id,
                    "status": status,
                    "cal": MAIN,
                    "ago": minutes_ago,
                    "start": f"2099-01-05T{hour}:00:00Z",
                    "end": f"2099-01-05T{hour}:50:00Z",
                },
            )
        conn.execute(
            text(
                "INSERT INTO external_calendar_events (id, user_id, source, source_event_id, "
                "source_series_id, calendar_id, start_at, end_at, title, answer, patient_id, "
                "appointment_id) VALUES (:id, :u, 'google_calendar', 'e1', 'wk', :cal, "
                "'2099-01-05T16:00:00Z', '2099-01-05T16:50:00Z', 'Jane Smith', 'client', :p, :appt)"
            ),
            {"id": str(uuid.uuid4()), "u": _A, "cal": MAIN, "p": patient_id, "appt": ids["dup"]},
        )
        for table in ("appointments", "external_calendar_events"):
            _set_rls(conn, schema, table, on=True)
    seeded.update(ids)

    yield seeded

    with engine.begin() as conn:
        conn.execute(text(f"DROP SCHEMA IF EXISTS {schema} CASCADE"))


def _outside_links(engine: Engine, schema: str) -> dict[str, tuple[str | None, str | None, str]]:
    rows = _past_rls(
        engine,
        schema,
        "appointments",
        "SELECT id::text, outside_source, outside_event_id, status FROM appointments",
    )
    return {r[0]: (r[1], r[2], r[3]) for r in rows}


def test_the_revision_only_adds(engine: Engine, tenant_at_parent: dict[str, str]) -> None:
    schema = tenant_at_parent["schema"]
    before = _answers_at_parent(engine, schema)

    result = upgrade_tenant_schema(engine, schema)

    assert result.status is TenantStatus.SUCCESS, result.detail
    assert {"scope", "answered_by_user_id", "session_clinician_user_id"} <= _columns(
        engine, schema, "patient_source_mappings"
    )
    assert "uq_patient_source_mappings_scoped" in _indexes(
        engine, schema, "patient_source_mappings"
    )
    assert {
        "uq_appointments_outside_event_per_calendar",
        "uq_appointments_outside_event_per_clinician",
    } <= _indexes(engine, schema, "appointments")
    assert _policies(engine, schema, "patient_source_mappings") == {"rls_practice_answers"}
    assert "rls_practice_directory_read" in _policies(engine, schema, "appointments")
    assert _function_owner(engine, schema) == _ROLE
    # The old rows are exactly as they were, with no scope: still their owner's.
    after = _all_answers(engine, schema)
    assert [row[:7] for row in after] == before
    assert {row[1] for row in after} == {None}


def test_duplicates_stop_following_the_event_and_nothing_is_cancelled(
    engine: Engine, tenant_at_parent: dict[str, str]
) -> None:
    schema = tenant_at_parent["schema"]

    upgrade_tenant_schema(engine, schema)

    links = _outside_links(engine, schema)
    assert links[tenant_at_parent["kept"]] == ("google_calendar", "e1", "confirmed")
    assert links[tenant_at_parent["dup"]] == (None, None, "confirmed")
    assert links[tenant_at_parent["cancelled"]] == ("google_calendar", "e1", "cancelled")
    [(linked,)] = _past_rls(
        engine,
        schema,
        "external_calendar_events",
        "SELECT appointment_id::text FROM external_calendar_events",
    )
    assert linked == tenant_at_parent["kept"]


def test_a_rerun_changes_nothing(engine: Engine, tenant_at_parent: dict[str, str]) -> None:
    schema = tenant_at_parent["schema"]
    upgrade_tenant_schema(engine, schema)
    before = (_all_answers(engine, schema), _outside_links(engine, schema))

    _downgrade(engine, schema, _PARENT)
    result = upgrade_tenant_schema(engine, schema)

    assert result.status is TenantStatus.SUCCESS, result.detail
    assert (_all_answers(engine, schema), _outside_links(engine, schema)) == before
    assert _function_owner(engine, schema) == _ROLE
    assert _policies(engine, schema, "patient_source_mappings") == {"rls_practice_answers"}


def test_the_downgrade_takes_only_what_it_added(
    engine: Engine, tenant_at_parent: dict[str, str]
) -> None:
    schema = tenant_at_parent["schema"]
    upgrade_tenant_schema(engine, schema)
    before = [row[:7] for row in _all_answers(engine, schema)]

    _downgrade(engine, schema, _PARENT)

    assert not {"scope", "answered_by_user_id", "session_clinician_user_id"} & _columns(
        engine, schema, "patient_source_mappings"
    )
    assert not {
        "uq_patient_source_mappings_scoped",
        "uq_appointments_outside_event_per_calendar",
        "uq_appointments_outside_event_per_clinician",
    } & (
        _indexes(engine, schema, "patient_source_mappings")
        | _indexes(engine, schema, "appointments")
    )
    assert _function_owner(engine, schema) is None
    assert "rls_practice_directory_read" not in _policies(engine, schema, "appointments")
    assert _policies(engine, schema, "patient_source_mappings") == {"rls_user_isolation"}
    assert _answers_at_parent(engine, schema) == before


# --- The policy, and adoption -----------------------------------------------------


@pytest.fixture
def practice(engine: Engine) -> Iterator[dict[str, str]]:
    """A practice at head: one chart both clinicians see, one old answer of A's."""
    schema = f"practice_test_answers_{uuid.uuid4().hex[:8]}"
    create_practice_schema(engine, schema)
    patient_id = _chart(engine, schema, "Pablo", "Bear", _A, _B)
    doc = _legacy_answer(engine, schema, _A, SH, "SH00001", patient_id)
    yield {"schema": schema, "patient": patient_id, "legacy_doc": doc}
    with engine.begin() as conn:
        conn.execute(text(f"DROP SCHEMA IF EXISTS {schema} CASCADE"))


def _visible_rows(sess: Session) -> int:
    return sess.execute(text("SELECT count(*) FROM patient_source_mappings")).scalar_one()


def _match_feed_code(sess: Session, user_id: str, code: str) -> str | None:
    ctx = MatchContext.for_practice(
        user_id, PostgresPatientRepository(sess), PostgresPatientSourceMappingRepository(sess)
    )
    hint = PatientHint(source=SH, source_identifier=code, scope=PRACTICE_SCOPE)
    return match_patient(hint, ctx).patient_id


class TestOldAnswersAreAdopted:
    def test_before_adoption_only_the_owner_sees_it(
        self, engine: Engine, practice: dict[str, str], opened: list[Session]
    ) -> None:
        schema = practice["schema"]
        mine = _session(engine, schema, _A)
        theirs = _session(engine, schema, _B)
        opened.extend([mine, theirs])

        assert _visible_rows(mine) == 1
        assert _visible_rows(theirs) == 0
        # Read as B, with nothing adopted: the old row is not B's to consult.
        assert _match_feed_code(theirs, _B, "SH00001") is None
        assert (
            PostgresPatientSourceMappingRepository(theirs).list_by_source(PRACTICE_SCOPE, SH) == []
        )

    def test_the_owners_first_read_adopts_it_for_the_practice(
        self, engine: Engine, practice: dict[str, str], opened: list[Session]
    ) -> None:
        schema = practice["schema"]
        mine = _session(engine, schema, _A)
        opened.append(mine)

        assert _match_feed_code(mine, _A, "SH00001") == practice["patient"]
        mine.commit()

        theirs = _session(engine, schema, _B)
        opened.append(theirs)
        assert _match_feed_code(theirs, _B, "SH00001") == practice["patient"]
        # Reading past the policy alters the table, which waits on any open
        # transaction that touched it: close B's first.
        theirs.commit()
        [row] = _all_answers(engine, schema)
        doc_id, scope, source, stored, patient_id, answer, user_id, answered_by, session_for = row
        assert (scope, source, stored, patient_id, answer) == (
            PRACTICE_SCOPE,
            SH,
            identifier_digest("SH00001"),
            practice["patient"],
            "client",
        )
        assert (user_id, answered_by, session_for) == (_A, _A, None)
        assert doc_id != practice["legacy_doc"]
        # Nothing in the table says "SH00001" any more.
        assert "sh00001" not in doc_id.lower()
        assert "sh00001" not in stored.lower()

    def test_a_second_read_changes_nothing(
        self, engine: Engine, practice: dict[str, str], opened: list[Session]
    ) -> None:
        schema = practice["schema"]
        mine = _session(engine, schema, _A)
        opened.append(mine)
        _match_feed_code(mine, _A, "SH00001")
        mine.commit()
        once = _all_answers(engine, schema)

        again = _session(engine, schema, _A)
        opened.append(again)
        _match_feed_code(again, _A, "SH00001")
        again.commit()

        assert _all_answers(engine, schema) == once

    def test_the_newer_answer_stands(
        self, engine: Engine, practice: dict[str, str], opened: list[Session]
    ) -> None:
        """B answered SH00001 for the practice after A's old row: B's answer holds."""
        schema = practice["schema"]
        other = _chart(engine, schema, "Lulu", "Niemi", _A, _B)
        theirs = _session(engine, schema, _B)
        opened.append(theirs)
        PostgresPatientSourceMappingRepository(theirs).save(
            PatientSourceMapping(PRACTICE_SCOPE, SH, identifier_digest("SH00001"), other, _B)
        )
        theirs.commit()
        # And an older practice answer for SH00002, which A's newer old row replaces.
        older = datetime.now(UTC) - timedelta(days=90)
        PostgresPatientSourceMappingRepository(theirs).save(
            PatientSourceMapping(
                PRACTICE_SCOPE, SH, identifier_digest("SH00002"), other, _B, created_at=older
            )
        )
        theirs.commit()
        _legacy_answer(engine, schema, _A, SH, "SH00002", practice["patient"], days_ago=10)

        mine = _session(engine, schema, _A)
        opened.append(mine)
        assert _match_feed_code(mine, _A, "SH00001") == other
        assert _match_feed_code(mine, _A, "SH00002") == practice["patient"]
        mine.commit()

        by_digest = {row[3]: row for row in _all_answers(engine, schema)}
        assert by_digest[identifier_digest("SH00001")][4:8] == (other, "client", _B, _B)
        assert by_digest[identifier_digest("SH00002")][4:8] == (
            practice["patient"],
            "client",
            _A,
            _A,
        )
        assert len(by_digest) == 2

    def test_two_requests_adopting_at_once_both_land(
        self, engine: Engine, practice: dict[str, str], opened: list[Session]
    ) -> None:
        schema = practice["schema"]
        first = _session(engine, schema, _A)
        second = _session(engine, schema, _A)
        opened.extend([first, second])

        PostgresPatientSourceMappingRepository(first).adopt_legacy(_A, SH, PRACTICE_SCOPE)
        done = threading.Event()
        outcome: list[Exception | int] = []

        def race() -> None:
            try:
                outcome.append(
                    PostgresPatientSourceMappingRepository(second).adopt_legacy(
                        _A, SH, PRACTICE_SCOPE
                    )
                )
                second.commit()
            except Exception as exc:
                outcome.append(exc)
            finally:
                done.set()

        thread = threading.Thread(target=race)
        thread.start()
        first.commit()
        assert done.wait(timeout=10), "the second adoption never finished"
        thread.join()

        assert not any(isinstance(o, Exception) for o in outcome), outcome
        [row] = _all_answers(engine, schema)
        assert row[1:4] == (PRACTICE_SCOPE, SH, identifier_digest("SH00001"))


class TestTheTableHoldsDigests:
    def test_an_answer_is_stored_as_a_digest_that_follows_the_key(
        self, engine: Engine, practice: dict[str, str], opened: list[Session], monkeypatch
    ) -> None:
        schema = practice["schema"]
        sess = _session(engine, schema, _A)
        opened.append(sess)
        outside = _outside(sess)
        title = "Zorbulax Quintwhistle"
        start = datetime(2099, 1, 5, 15, tzinfo=UTC)
        PostgresExternalCalendarEventRepository(sess).save(
            ExternalCalendarEvent(
                id=str(uuid.uuid4()),
                user_id=_A,
                source=ical_source(SH),
                source_event_id="sh-9",
                start_at=start,
                end_at=start + timedelta(minutes=50),
                title=title,
            )
        )
        outside.answer(_A, ical_source(SH), title, patient_id=practice["patient"])
        sess.commit()
        # The fixture's old SH00001 answer is adopted on the way; it is not this one.
        adopted = identifier_digest("SH00001")
        under_one_key = {row[3] for row in _all_answers(engine, schema) if row[1] is not None} - {
            adopted
        }

        monkeypatch.setenv("GOOGLE_CALENDAR_ENCRYPTION_KEY", _new_key())
        get_settings.cache_clear()
        again = _session(engine, schema, _A)
        opened.append(again)
        _outside(again).answer(_A, ical_source(SH), title, patient_id=practice["patient"])
        again.commit()
        under_another = {row[3] for row in _all_answers(engine, schema) if row[1] is not None} - {
            adopted
        }

        assert len(under_one_key) == 1
        assert under_another > under_one_key
        for row in _all_answers(engine, schema):
            for value in row:
                assert value is None or "zorbulax" not in str(value).lower()
        assert all(stored.startswith("feed:") for stored in under_another)

    def test_nothing_is_visible_with_no_clinician_armed(
        self, engine: Engine, practice: dict[str, str]
    ) -> None:
        schema = practice["schema"]
        with engine.connect() as conn:
            conn.execute(text(f"SET search_path = {schema}, {PLATFORM_SCHEMA}, public"))
            conn.execute(text("RESET app.current_user_id"))
            assert conn.execute(text("SELECT count(*) FROM patient_source_mappings")).scalar() == 0


# --- Two followers of one calendar -----------------------------------------------


def _open_row(user_id: str, event_id: str = "e1") -> ExternalCalendarEvent:
    start = datetime(2099, 1, 5, 15, tzinfo=UTC)
    return ExternalCalendarEvent(
        id=str(uuid.uuid4()),
        user_id=user_id,
        source=GOOGLE_CALENDAR_SOURCE,
        source_event_id=event_id,
        source_series_id="wk",
        calendar_id=MAIN,
        start_at=start,
        end_at=start + timedelta(minutes=50),
        title="Pablo Bear",
    )


class TestTwoFollowersOfOneCalendar:
    def test_one_answer_books_once_and_the_other_links_through_the_lookup(
        self, engine: Engine, practice: dict[str, str], opened: list[Session]
    ) -> None:
        schema = practice["schema"]
        series = calendar_source_identifier("wk", "", 0, "00:00")
        mine = _session(engine, schema, _A)
        theirs = _session(engine, schema, _B)
        opened.extend([mine, theirs])
        PostgresExternalCalendarEventRepository(mine).save(_open_row(_A))
        PostgresExternalCalendarEventRepository(theirs).save(_open_row(_B))
        mine.commit()
        theirs.commit()

        [a_row] = _outside(mine).answer(
            _A, GOOGLE_CALENDAR_SOURCE, series, patient_id=practice["patient"]
        )
        mine.commit()
        [b_row] = _outside(theirs).answer(
            _B, GOOGLE_CALENDAR_SOURCE, series, patient_id=practice["patient"]
        )
        theirs.commit()

        assert a_row.appointment_id is not None
        assert (b_row.answer, b_row.appointment_id) == (ANSWER_CLIENT, a_row.appointment_id)
        # B cannot read A's appointment, only learn its id for the link.
        assert PostgresAppointmentRepository(theirs).get(a_row.appointment_id, _B) is None
        assert (
            theirs.execute(
                text("SELECT count(*) FROM appointments WHERE outside_event_id = 'e1'")
            ).scalar_one()
            == 0
        )
        assert (
            PostgresAppointmentRepository(theirs).outside_appointment_id(
                GOOGLE_CALENDAR_SOURCE, MAIN, "e1", _B
            )
            == a_row.appointment_id
        )
        theirs.commit()  # the reads above hold a lock _past_rls's ALTER TABLE waits on
        live = _past_rls(
            engine,
            schema,
            "appointments",
            "SELECT id::text FROM appointments WHERE outside_event_id = 'e1' "
            "AND status <> 'cancelled'",
        )
        assert [r[0] for r in live] == [a_row.appointment_id]
        # The answer itself is the calendar's, given by A.
        [stored] = [row for row in _all_answers(engine, schema) if row[1] == calendar_scope(MAIN)]
        assert stored[6:9] == (_A, _A, _A)

    def test_the_lookup_answers_nothing_with_no_clinician_armed(
        self, engine: Engine, practice: dict[str, str], opened: list[Session]
    ) -> None:
        schema = practice["schema"]
        mine = _session(engine, schema, _A)
        opened.append(mine)
        PostgresAppointmentRepository(mine).create(
            _appointment(_A, "e1", MAIN, practice["patient"])
        )
        mine.commit()

        with engine.connect() as conn:
            conn.execute(text(f"SET search_path = {schema}, {PLATFORM_SCHEMA}, public"))
            conn.execute(text("RESET app.current_user_id"))
            found = conn.execute(
                text("SELECT practice_outside_appointment('google_calendar', :cal, 'e1')"),
                {"cal": MAIN},
            ).scalar()
        assert found is None

    def test_a_second_live_appointment_for_one_event_is_refused_even_across_a_race(
        self, engine: Engine, practice: dict[str, str], opened: list[Session]
    ) -> None:
        schema = practice["schema"]
        mine = _session(engine, schema, _A)
        theirs = _session(engine, schema, _B)
        opened.extend([mine, theirs])
        PostgresAppointmentRepository(mine).create(
            _appointment(_A, "e1", MAIN, practice["patient"])
        )
        done = threading.Event()
        outcome: list[Exception | Appointment] = []

        def race() -> None:
            try:
                outcome.append(
                    PostgresAppointmentRepository(theirs).create(
                        _appointment(_B, "e1", MAIN, practice["patient"])
                    )
                )
            except Exception as exc:
                outcome.append(exc)
            finally:
                done.set()

        thread = threading.Thread(target=race)
        thread.start()
        # B's insert waits on A's uncommitted row; A commits, and B is refused.
        mine.commit()
        assert done.wait(timeout=10), "the second insert never finished"
        thread.join()

        [result] = outcome
        assert isinstance(result, OutsideEventAlreadyBookedError)
        # B's session is still usable: the refusal undid only its own insert.
        assert theirs.execute(text("SELECT 1")).scalar_one() == 1

    def test_a_feeds_event_is_one_appointment_per_clinician(
        self, engine: Engine, practice: dict[str, str], opened: list[Session]
    ) -> None:
        schema = practice["schema"]
        mine = _session(engine, schema, _A)
        theirs = _session(engine, schema, _B)
        opened.extend([mine, theirs])

        PostgresAppointmentRepository(mine).create(
            _appointment(_A, "sh-1", None, practice["patient"])
        )
        mine.commit()
        # A colleague's own feed carries the same uid: their diary, their session.
        PostgresAppointmentRepository(theirs).create(
            _appointment(_B, "sh-1", None, practice["patient"])
        )
        theirs.commit()
        with pytest.raises(OutsideEventAlreadyBookedError):
            PostgresAppointmentRepository(mine).create(
                _appointment(_A, "sh-1", None, practice["patient"])
            )
