# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""The practice client directory, and nothing wider, proven against real Postgres.

``practice_client_directory()`` is the one read of ``patients`` that sees past
a clinician's grants, so that matching recognises a colleague's client instead
of making a second chart. Everything here is about keeping it that narrow:

* the function is owned by ``pablo_practice_directory``, which cannot log in
  or bypass RLS, and which the app role can hand ownership to without
  inheriting anything from it;
* it returns exactly its six columns, only for live charts, only in its own
  schema, and nothing at all to a session with no clinician armed;
* every other read of ``patients`` and ``patient_clinicians`` is unchanged —
  a clinician still sees only their own grants — and even ``SET ROLE`` to the
  directory role reaches only the listed columns;
* the matcher over it recognises a colleague's client and says who sees
  them, and the import refuses to chart or book that client.

Runs against schemas built by the real ``create_practice_schema``, as the
``pablo`` role the integration conftest creates ``NOSUPERUSER NOBYPASSRLS``.
The platform chain that made the directory role ran as that same role, so the
non-superuser path through it is exercised too. Every invisibility assertion
has a visibility control beside it on the same kind of connection.

Run: ``make test-integration``.
"""

from __future__ import annotations

import importlib.util
import os
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING
from unittest.mock import MagicMock

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, make_url, text
from sqlalchemy.exc import DBAPIError, ProgrammingError
from sqlalchemy.orm import Session

if TYPE_CHECKING:
    from collections.abc import Iterator
    from types import ModuleType

    from app.calendar_providers.practice_import import ImportProposal
    from app.repositories.postgres.patient import PostgresPatientRepository
    from app.repositories.postgres.patient_source_mapping import (
        PostgresPatientSourceMappingRepository,
    )
    from sqlalchemy.engine import Connection, Engine

_DB_URL = os.environ.get("DATABASE_URL", "")
pytestmark = pytest.mark.skipif(
    not _DB_URL or os.environ.get("DATABASE_BACKEND") != "postgres",
    reason=(
        "PostgreSQL not configured. Set DATABASE_URL and DATABASE_BACKEND=postgres "
        "or run via make test-integration."
    ),
)

_ROLE = "pablo_practice_directory"
_A = "0b5c2e81-3f6d-5a47-9c18-4e7d2a90b1c3"
_B = "7e14a9d2-6c0b-5f83-a2d5-91b6c3e8f047"
_FAMILY = "family@example.com"
_COLUMNS = ["id", "first_name", "last_name", "date_of_birth", "email", "clinician_ids"]


@pytest.fixture(scope="module")
def engine() -> Iterator[Engine]:
    backend_dir = Path(__file__).resolve().parents[2]
    cfg = Config(str(backend_dir / "alembic.ini"))
    cfg.set_main_option("script_location", str(backend_dir / "alembic"))
    command.upgrade(cfg, "head")
    eng = create_engine(_DB_URL, pool_pre_ping=True)
    yield eng
    eng.dispose()


def _new_schema(engine: Engine, label: str) -> str:
    from app.db.provisioning import create_practice_schema  # noqa: PLC0415

    with engine.connect() as conn:
        conn.execute(text("SET search_path = practice, platform, public"))
        conn.commit()
    schema = f"practice_test_{label}_{uuid.uuid4().hex[:8]}"
    create_practice_schema(engine, schema)
    return schema


def _drop(engine: Engine, schema: str) -> None:
    with engine.connect() as conn:
        conn.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        conn.commit()


def _as(engine: Engine, schema: str, user_id: str | None) -> Connection:
    conn = engine.connect()
    conn.execute(text(f"SET search_path = {schema}, platform, public"))
    conn.execute(text("RESET app.current_patient_id"))
    if user_id is None:
        conn.execute(text("RESET app.current_user_id"))
    else:
        conn.execute(text("SELECT set_config('app.current_user_id', :u, false)"), {"u": user_id})
    return conn


@dataclass(frozen=True)
class _Chart:
    first: str
    last: str
    email: str | None = None
    status: str = "active"
    deleted: bool = False


def _chart(engine: Engine, schema: str, clinician: str, chart: _Chart) -> str:
    """A chart created by ``clinician``, holding its primary grant, as the app writes it."""
    patient_id = str(uuid.uuid4())
    conn = _as(engine, schema, clinician)
    try:
        conn.execute(
            text(
                "INSERT INTO patients (id, first_name, last_name, first_name_lower, "
                "last_name_lower, email, status, session_count, date_of_birth, deleted_at, "
                "created_at, updated_at) VALUES (CAST(:pid AS uuid), :first, :last, "
                "lower(:first), lower(:last), :email, :status, 0, DATE '1980-01-02', "
                "CASE WHEN :deleted THEN now() END, now(), now())"
            ),
            {
                "pid": patient_id,
                "first": chart.first,
                "last": chart.last,
                "email": chart.email,
                "status": chart.status,
                "deleted": chart.deleted,
            },
        )
        conn.execute(
            text(
                "INSERT INTO patient_clinicians (patient_id, user_id, granted_by) "
                "VALUES (CAST(:pid AS uuid), :u, :u)"
            ),
            {"pid": patient_id, "u": clinician},
        )
        conn.commit()
    finally:
        conn.close()
    return patient_id


@pytest.fixture(scope="module")
def practice(engine: Engine) -> Iterator[dict[str, str]]:
    """One practice, two clinicians. ``shared_*`` share a family email."""
    schema = _new_schema(engine, "directory")
    charts = {
        "schema": schema,
        "mine": _chart(engine, schema, _A, _Chart("Ada", "Lovelace")),
        "theirs": _chart(engine, schema, _B, _Chart("Grace", "Hopper")),
        "shared_mine": _chart(engine, schema, _A, _Chart("Ana", "Ruiz", _FAMILY)),
        "shared_theirs": _chart(engine, schema, _B, _Chart("Leo", "Ruiz", _FAMILY)),
        "deleted": _chart(engine, schema, _B, _Chart("Gone", "Away", deleted=True)),
        "pending": _chart(engine, schema, _B, _Chart("Not", "Yet", status="pending")),
    }
    yield charts
    _drop(engine, schema)


def _directory(conn: Connection) -> dict[str, list[str]]:
    rows = conn.execute(text("SELECT id, clinician_ids FROM practice_client_directory()")).all()
    return {str(r.id): sorted(str(u) for u in r.clinician_ids) for r in rows}


class TestTheRoles:
    def test_the_app_role_does_not_bypass_rls(self, engine: Engine) -> None:
        """If this fails, every isolation assertion here is meaningless."""
        with engine.connect() as conn:
            row = conn.execute(
                text("SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname = current_user")
            ).one()
        assert (row.rolsuper, row.rolbypassrls) == (False, False)

    def test_the_directory_role_cannot_log_in_or_bypass_rls(self, engine: Engine) -> None:
        with engine.connect() as conn:
            row = conn.execute(
                text("SELECT rolcanlogin, rolbypassrls, rolsuper FROM pg_roles WHERE rolname = :r"),
                {"r": _ROLE},
            ).one()
        assert (row.rolcanlogin, row.rolbypassrls, row.rolsuper) == (False, False, False)

    def test_the_app_role_can_hand_it_ownership_but_inherits_nothing(self, engine: Engine) -> None:
        with engine.connect() as conn:
            row = conn.execute(
                text(
                    "SELECT pg_has_role(current_user, :r, 'SET') AS can_set, "
                    "pg_has_role(current_user, :r, 'USAGE') AS inherits"
                ),
                {"r": _ROLE},
            ).one()
        assert row.can_set is True
        assert row.inherits is False


def _platform_revision() -> ModuleType:
    path = (
        Path(__file__).resolve().parents[2]
        / "alembic_platform"
        / "versions"
        / "d4a7e2c91b06_practice_directory_role.py"
    )
    spec = importlib.util.spec_from_file_location("practice_directory_role_revision", path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TestTheMigrationFailsLoudly:
    def test_a_user_who_cannot_be_granted_the_role_is_told_how_to_fix_it(
        self, engine: Engine
    ) -> None:
        sql = _platform_revision().ENSURE_ROLE_SQL
        user = f"no_createrole_{uuid.uuid4().hex[:8]}"
        password = uuid.uuid4().hex
        with engine.connect() as conn:
            conn.execute(text(f"CREATE ROLE {user} LOGIN NOCREATEROLE PASSWORD '{password}'"))
            conn.commit()
        limited = create_engine(
            make_url(_DB_URL).set(username=user, password=password), pool_pre_ping=True
        )
        try:
            with limited.connect() as conn, pytest.raises(DBAPIError) as raised:
                conn.execute(text(sql))
            message = str(raised.value)
            assert "cannot be granted pablo_practice_directory" in message
            assert "docs/SELF_HOSTING_HIPAA_GUIDE.md, Database roles" in message
        finally:
            limited.dispose()
            with engine.connect() as conn:
                conn.execute(text(f"DROP ROLE {user}"))
                conn.commit()

    def test_it_is_a_no_op_for_the_user_that_already_holds_it(self, engine: Engine) -> None:
        with engine.connect() as conn:
            conn.execute(text(_platform_revision().ENSURE_ROLE_SQL))
            conn.commit()


class TestTheFunction:
    def test_it_is_owned_by_the_directory_role_with_a_pinned_path(
        self, engine: Engine, practice: dict[str, str]
    ) -> None:
        with engine.connect() as conn:
            row = conn.execute(
                text(
                    "SELECT pg_get_userbyid(proowner) AS owner, prosecdef, proconfig "
                    "FROM pg_proc WHERE oid = to_regprocedure(:fn)"
                ),
                {"fn": f"{practice['schema']}.practice_client_directory()"},
            ).one()
        assert row.owner == _ROLE
        assert row.prosecdef is True
        assert row.proconfig == ["search_path=pg_catalog, pg_temp"]

    def test_it_returns_only_its_listed_columns(
        self, engine: Engine, practice: dict[str, str]
    ) -> None:
        conn = _as(engine, practice["schema"], _A)
        try:
            result = conn.execute(text("SELECT * FROM practice_client_directory()"))
            assert list(result.keys()) == _COLUMNS
        finally:
            conn.close()

    def test_it_shows_every_live_chart_with_who_sees_it(
        self, engine: Engine, practice: dict[str, str]
    ) -> None:
        conn = _as(engine, practice["schema"], _A)
        try:
            assert _directory(conn) == {
                practice["mine"]: [_A],
                practice["theirs"]: [_B],
                practice["shared_mine"]: [_A],
                practice["shared_theirs"]: [_B],
            }
        finally:
            conn.close()

    def test_it_returns_nothing_without_a_clinician(
        self, engine: Engine, practice: dict[str, str]
    ) -> None:
        conn = _as(engine, practice["schema"], None)
        try:
            assert _directory(conn) == {}
        finally:
            conn.close()

    def test_it_reads_only_its_own_practice(self, engine: Engine, practice: dict[str, str]) -> None:
        other = _new_schema(engine, "directory_other")
        try:
            elsewhere = _chart(engine, other, _A, _Chart("Ada", "Lovelace"))
            conn = _as(engine, other, _A)
            try:
                assert _directory(conn) == {elsewhere: [_A]}
            finally:
                conn.close()
            conn = _as(engine, practice["schema"], _A)
            try:
                assert elsewhere not in _directory(conn)
            finally:
                conn.close()
        finally:
            _drop(engine, other)


class TestNothingElseWidens:
    def test_a_clinician_still_reads_only_their_own_charts(
        self, engine: Engine, practice: dict[str, str]
    ) -> None:
        for me, mine, theirs in ((_A, "mine", "theirs"), (_B, "theirs", "mine")):
            conn = _as(engine, practice["schema"], me)
            try:
                seen = {str(r) for r in conn.execute(text("SELECT id FROM patients")).scalars()}
                assert practice[mine] in seen
                assert practice[theirs] not in seen
                grants = {
                    str(r)
                    for r in conn.execute(text("SELECT user_id FROM patient_clinicians")).scalars()
                }
                assert grants == {me}
            finally:
                conn.close()

    def test_the_directory_role_reaches_only_the_listed_columns(
        self, engine: Engine, practice: dict[str, str]
    ) -> None:
        conn = _as(engine, practice["schema"], _A)
        try:
            conn.execute(text(f"SET ROLE {_ROLE}"))
            # The control: what the function reads is readable.
            names = conn.execute(text("SELECT first_name FROM patients")).scalars().all()
            assert "Grace" in names
            conn.execute(text("RESET ROLE"))
            conn.commit()
            for denied in (
                "SELECT diagnosis FROM patients",
                "SELECT phone FROM patients",
                "SELECT address_line1 FROM patients",
                "SELECT role FROM patient_clinicians",
                "SELECT id FROM notes",
                "SELECT id FROM appointments",
            ):
                conn.execute(text(f"SET ROLE {_ROLE}"))
                with pytest.raises(ProgrammingError, match="permission denied"):
                    conn.execute(text(denied))
                conn.rollback()
        finally:
            conn.close()


# --- Matching over the directory -------------------------------------------------


def _tenant_session(engine: Engine, schema: str, user_id: str) -> tuple[Connection, Session]:
    conn = _as(engine, schema, user_id)
    return conn, Session(bind=conn)


def _repos(
    session: Session,
) -> tuple[PostgresPatientRepository, PostgresPatientSourceMappingRepository]:
    from app.repositories.postgres.patient import PostgresPatientRepository  # noqa: PLC0415
    from app.repositories.postgres.patient_source_mapping import (  # noqa: PLC0415
        PostgresPatientSourceMappingRepository,
    )

    return PostgresPatientRepository(session), PostgresPatientSourceMappingRepository(session)


def _users() -> MagicMock:
    from app.models import User  # noqa: PLC0415

    users = MagicMock()
    users.get.side_effect = lambda uid: (
        User(
            id=uid, email="b@example.com", name="Hopper", title="Dr.", created_at=datetime.now(UTC)
        )
        if uid == _B
        else None
    )
    users.get_preferences.return_value.model_fields_set = set()
    return users


def _proposal(summary: str) -> ImportProposal:
    from app.calendar_providers.practice_import import build_proposal  # noqa: PLC0415
    from app.calendar_providers.provider import ImportCandidate  # noqa: PLC0415

    now = datetime.now(UTC)
    first = now - timedelta(days=21)
    return build_proposal(
        [
            ImportCandidate(
                provider_event_id=f"evt-{i}",
                start=first + timedelta(days=7 * i),
                end=first + timedelta(days=7 * i, minutes=50),
                summary=summary,
                attendee_count=0,
                series_id="rec-1",
            )
            for i in range(6)
        ],
        now=now,
        timezone="UTC",
    )


def _live_charts(engine: Engine, schema: str) -> set[str]:
    conn = _as(engine, schema, _A)
    try:
        return set(_directory(conn))
    finally:
        conn.close()


class TestMatchingAColleaguesClient:
    def test_an_import_of_their_client_says_who_sees_them_and_creates_nothing(
        self, engine: Engine, practice: dict[str, str]
    ) -> None:
        from app.patients.matching import MatchContext  # noqa: PLC0415
        from app.patients.seen_by import SeenBy  # noqa: PLC0415
        from app.routes.calendar_import import _to_response  # noqa: PLC0415

        before = _live_charts(engine, practice["schema"])
        conn, session = _tenant_session(engine, practice["schema"], _A)
        try:
            patients, mappings = _repos(session)
            ctx = MatchContext.for_practice(_A, patients, mappings)
            response = _to_response(_proposal("Grace Hopper"), ctx, SeenBy(_users()))
        finally:
            session.close()
            conn.close()

        [series] = response.series
        assert series.match.seen_by == ["Dr. Hopper"]
        assert series.match.patient is None
        assert series.match.possible == []
        assert series.preselected is False
        assert _live_charts(engine, practice["schema"]) == before

    def test_a_confirm_naming_their_chart_is_refused(
        self, engine: Engine, practice: dict[str, str]
    ) -> None:
        from app.api_errors import BadRequestError  # noqa: PLC0415
        from app.auth.service import TenantContext  # noqa: PLC0415
        from app.models.scheduling import (  # noqa: PLC0415
            ConfirmImportRequest,
            ConfirmImportSeries,
        )
        from app.routes.calendar_import import confirm_calendar_import  # noqa: PLC0415

        before = _live_charts(engine, practice["schema"])
        scheduling = MagicMock()
        start = datetime.now(UTC) + timedelta(days=3)
        conn, session = _tenant_session(engine, practice["schema"], _A)
        try:
            patients, mappings = _repos(session)
            for patient_id in (practice["theirs"], None):
                with pytest.raises(BadRequestError):
                    confirm_calendar_import(
                        http_request=MagicMock(),
                        request=ConfirmImportRequest(
                            series=[
                                ConfirmImportSeries(
                                    candidate_key="k1",
                                    display_name="Grace Hopper",
                                    patient_id=patient_id,
                                    start_at=start,
                                    duration_minutes=50,
                                    cadence="weekly",
                                    occurrences=2,
                                )
                            ]
                        ),
                        ctx=TenantContext(user_id=_A),
                        user=MagicMock(),
                        patient_repo=patients,
                        mappings=mappings,
                        scheduling=scheduling,
                        user_repo=_users(),
                        audit=MagicMock(),
                        owner_tz=UTC,
                    )
        finally:
            session.close()
            conn.close()

        scheduling.create_recurring.assert_not_called()
        assert _live_charts(engine, practice["schema"]) == before

    def test_my_own_client_still_matches(self, engine: Engine, practice: dict[str, str]) -> None:
        from app.patients.matching import MatchContext, PatientHint, match_patient  # noqa: PLC0415

        conn, session = _tenant_session(engine, practice["schema"], _A)
        try:
            patients, mappings = _repos(session)
            ctx = MatchContext.for_practice(_A, patients, mappings)
            result = match_patient(PatientHint(full_name="Ada Lovelace"), ctx)
        finally:
            session.close()
            conn.close()

        assert result.patient_id == practice["mine"]
        assert result.visible

    def test_an_email_two_charts_share_is_possible_not_certain(
        self, engine: Engine, practice: dict[str, str]
    ) -> None:
        from app.patients.matching import MatchContext, PatientHint, match_patient  # noqa: PLC0415

        conn, session = _tenant_session(engine, practice["schema"], _A)
        try:
            patients, mappings = _repos(session)
            ctx = MatchContext.for_practice(_A, patients, mappings)
            result = match_patient(PatientHint(email=_FAMILY), ctx)
        finally:
            session.close()
            conn.close()

        assert result.patient_id is None
        assert sorted(result.possible_ids) == sorted(
            [practice["shared_mine"], practice["shared_theirs"]]
        )
        assert result.hidden_ids == [practice["shared_theirs"]]
