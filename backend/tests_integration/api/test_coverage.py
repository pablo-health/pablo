# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Coverage of an away period, on the real stack and under row-level security.

One practice, three clinicians: one going away, the colleague asked to cover,
and a third who is neither. Clients: two of the away clinician's, one of the
colleague's own, and one of the third's.

The database role these tests connect as cannot bypass row-level security,
so every "can the colleague see this client" question below is answered by
the shipped policies and ``has_patient_access``, asked on a session armed as
the colleague the way a request arms it.

What each test would catch: any grant before the colleague accepts, or after
a decline; access before ``starts_at`` or after ``expires_at``; access to a
client who is not the away clinician's; access surviving an early end, or a
change of dates; a client added during the window left uncovered; a second
overlapping coverage silently overwriting the first's window; the colleague's
activity not marked as coverage in the audit log, or readable by anybody but
the clinician whose clients it was; and anybody but the named colleague
answering.

Run: ``make test-integration``.
"""

from __future__ import annotations

import os
import uuid
from datetime import UTC, date, datetime, timedelta
from typing import TYPE_CHECKING

import pytest
from sqlalchemy import text

if TYPE_CHECKING:
    from collections.abc import Iterator

    from fastapi import FastAPI, Request
    from fastapi.testclient import TestClient
    from sqlalchemy.engine import Connection, Engine

_db_url = os.environ.get("DATABASE_URL", "")
pytestmark = pytest.mark.skipif(
    not _db_url or os.environ.get("DATABASE_BACKEND") != "postgres",
    reason=(
        "PostgreSQL not configured. Set DATABASE_URL and "
        "DATABASE_BACKEND=postgres; testcontainers should set both."
    ),
)

os.environ.setdefault("ENVIRONMENT", "development")

_AWAY = "3c8e1f52-7a49-4d06-b2e1-9f4a6c0d8b17"
_COVER = "8a1d4b97-2e63-4c5f-a908-1b7e3d6c4f22"
_THIRD = "5f2b9c08-4d71-4e3a-8c65-7a0e2b9d1c43"
_CLINICIANS = {
    _AWAY: ("coverage-away@example.com", "Robin Avery"),
    _COVER: ("coverage-cover@example.com", "Sam Okafor"),
    _THIRD: ("coverage-third@example.com", "Jo Lindqvist"),
}
_REVIEWED = "Thanks for writing. I'm away for a few days."


class _Seed:
    def __init__(self) -> None:
        self.away = [str(uuid.uuid4()) for _ in range(2)]
        self.covers_own = str(uuid.uuid4())
        self.thirds = str(uuid.uuid4())


@pytest.fixture(scope="module")
def tenant_schema(engine: Engine) -> Iterator[str]:
    """A practice with three clinicians, all mapped to it as members."""
    from app.db.platform_models import (  # noqa: PLC0415
        EmailTenantMappingRow,
        PlatformUserRow,
        PracticeRow,
    )
    from app.db.provisioning import create_practice_schema  # noqa: PLC0415
    from sqlalchemy.orm import Session as OrmSession  # noqa: PLC0415

    with engine.connect() as conn:
        conn.execute(text("SET search_path = practice, platform, public"))
        conn.commit()
    practice_id = str(uuid.uuid4())
    schema = f"practice_{practice_id.replace('-', '_')}"
    now = datetime.now(UTC)
    create_practice_schema(engine, schema)
    with OrmSession(engine) as session:
        session.add(
            PracticeRow(
                id=practice_id,
                name="Coverage",
                schema_name=schema,
                owner_email=_CLINICIANS[_AWAY][0],
                owner_user_id=_AWAY,
                created_at=now,
            )
        )
        for user_id, (email, name) in _CLINICIANS.items():
            session.add(PlatformUserRow(id=user_id, email=email, name=name, created_at=now))
            session.add(
                EmailTenantMappingRow(
                    email=email, tenant_id=practice_id, practice_id=practice_id, created_at=now
                )
            )
        session.commit()
    yield schema
    with OrmSession(engine) as session:
        for user_id, (email, _name) in _CLINICIANS.items():
            session.query(EmailTenantMappingRow).filter_by(email=email).delete()
            session.query(PlatformUserRow).filter_by(id=user_id).delete()
        session.query(PracticeRow).filter_by(id=practice_id).delete()
        session.commit()
    with engine.connect() as conn:
        conn.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        conn.commit()


def _scoped(conn: Connection, schema: str, user_id: str) -> None:
    """Scope *conn* to the practice and arm it as *user_id*, as a request would."""
    conn.execute(text(f"SET search_path = {schema}, platform, public"))
    conn.execute(text("SELECT set_config('app.current_user_id', :u, true)"), {"u": user_id})


@pytest.fixture(scope="module")
def seed(engine: Engine, tenant_schema: str) -> _Seed:
    s = _Seed()
    charts = [(pid, _AWAY) for pid in s.away] + [(s.covers_own, _COVER), (s.thirds, _THIRD)]
    for i, (pid, owner) in enumerate(charts):
        with engine.begin() as conn:
            _scoped(conn, tenant_schema, owner)
            conn.execute(
                text(
                    "INSERT INTO patients (id, first_name, last_name, first_name_lower, "
                    "last_name_lower, status, session_count, created_at, updated_at) "
                    "VALUES (CAST(:pid AS uuid), :first, 'Client', lower(:first), 'client', "
                    "'active', 0, now(), now())"
                ),
                {"pid": pid, "first": f"Covered{i}"},
            )
            conn.execute(
                text(
                    "INSERT INTO patient_clinicians (patient_id, user_id, granted_by, role) "
                    "VALUES (CAST(:pid AS uuid), :u, :u, 'primary')"
                ),
                {"pid": pid, "u": owner},
            )
    return s


@pytest.fixture
def clean(engine: Engine, tenant_schema: str, seed: _Seed) -> Iterator[None]:
    """No coverage, covering grant or added client carries over between tests."""
    yield
    with engine.begin() as conn:
        _scoped(conn, tenant_schema, _COVER)
        conn.execute(text("DELETE FROM patient_clinicians WHERE role = 'covering'"))
    with engine.begin() as conn:
        _scoped(conn, tenant_schema, _AWAY)
        conn.execute(
            text("UPDATE patients SET deleted_at = now() WHERE id <> ALL(CAST(:keep AS uuid[]))"),
            {"keep": seed.away},
        )
    with engine.begin() as conn:
        conn.execute(text(f"SET search_path = {tenant_schema}, platform, public"))
        conn.execute(text("DELETE FROM clinician_away_periods"))


@pytest.fixture
def as_clinician(
    fastapi_app: FastAPI, tenant_schema: str, monkeypatch: pytest.MonkeyPatch
) -> Iterator[dict[str, str]]:
    """The real route stack, signed in as whichever clinician ``acting["id"]`` names."""
    from app.auth.providers import VerifiedIdentity  # noqa: PLC0415
    from app.auth.service import (  # noqa: PLC0415
        TenantContext,
        get_current_user,
        get_current_user_id,
        get_current_user_no_mfa,
        get_tenant_context,
        require_active_subscription,
        require_baa_acceptance,
    )
    from app.db import arm_current_user_id, get_db_session  # noqa: PLC0415
    from app.models import User  # noqa: PLC0415

    acting = {"id": _AWAY}

    def _user() -> User:
        email, name = _CLINICIANS[acting["id"]]
        return User(
            id=acting["id"],
            email=email,
            name=name,
            created_at=datetime(2024, 1, 1, tzinfo=UTC),
            baa_accepted_at=datetime(2024, 1, 1, tzinfo=UTC),
            baa_version="2024-01-01",
        )

    def _stash_identity(request: Request) -> None:
        email, _name = _CLINICIANS[acting["id"]]
        request.state.verified_identity = VerifiedIdentity(
            provider="test",
            subject_id=acting["id"],
            email=email,
            email_verified=True,
            mfa_satisfied=True,
            claims={},
        )

    monkeypatch.setattr("app.db.middleware._verify_and_stash_clinician_identity", _stash_identity)

    def _armed_user() -> User:
        arm_current_user_id(get_db_session(), acting["id"])
        return _user()

    def _tenant_context() -> TenantContext:
        arm_current_user_id(get_db_session(), acting["id"])
        return TenantContext(
            user_id=acting["id"], practice_id="test-tenant", practice_schema=tenant_schema
        )

    fastapi_app.dependency_overrides[get_current_user_id] = lambda: acting["id"]
    fastapi_app.dependency_overrides[get_current_user] = _armed_user
    fastapi_app.dependency_overrides[get_current_user_no_mfa] = _armed_user
    fastapi_app.dependency_overrides[require_active_subscription] = _armed_user
    fastapi_app.dependency_overrides[require_baa_acceptance] = _armed_user
    fastapi_app.dependency_overrides[get_tenant_context] = _tenant_context
    try:
        yield acting
    finally:
        fastapi_app.dependency_overrides.clear()


@pytest.fixture
def client(fastapi_app: FastAPI, as_clinician: dict[str, str]) -> TestClient:
    from fastapi.testclient import TestClient  # noqa: PLC0415

    return TestClient(fastapi_app)


def _today() -> date:
    return datetime.now(UTC).date()


def _yesterday() -> date:
    """A first day away already begun on the clinician's calendar, whatever their zone."""
    return _today() - timedelta(days=1)


def _set_away(
    client: TestClient, acting: dict[str, str], *, start: date, end: date, cover: str = _COVER
) -> dict:
    acting["id"] = _AWAY
    created = client.post(
        "/api/away-periods",
        json={
            "start_date": start.isoformat(),
            "end_date": end.isoformat(),
            "covering_user_id": cover,
            "acknowledgment_text": _REVIEWED,
        },
    )
    assert created.status_code == 201, created.text
    return created.json()


def _away_now(client: TestClient, acting: dict[str, str], *, days: int = 5) -> dict:
    """Away dates already under way, asking the colleague to cover."""
    return _set_away(client, acting, start=_yesterday(), end=_today() + timedelta(days=days))


def _answer(client: TestClient, acting: dict[str, str], period_id: str, answer: str) -> dict:
    acting["id"] = _COVER
    answered = client.post(f"/api/covering/{period_id}/{answer}")
    assert answered.status_code == 200, answered.text
    return answered.json()


def _sees(engine: Engine, schema: str, user_id: str, patient_id: str) -> bool:
    """Whether *user_id* can read the chart, through the row policy on ``patients``."""
    with engine.begin() as conn:
        _scoped(conn, schema, user_id)
        return (
            conn.execute(
                text("SELECT 1 FROM patients WHERE id = CAST(:p AS uuid)"), {"p": patient_id}
            ).first()
            is not None
        )


def _has_access(engine: Engine, schema: str, user_id: str, patient_id: str) -> bool:
    with engine.begin() as conn:
        _scoped(conn, schema, user_id)
        return bool(
            conn.execute(
                text("SELECT has_patient_access(CAST(:p AS uuid), :u)"),
                {"p": patient_id, "u": user_id},
            ).scalar()
        )


def _covering_grants(engine: Engine, schema: str) -> list[tuple]:
    """The colleague's covering grants, read as the colleague."""
    with engine.begin() as conn:
        _scoped(conn, schema, _COVER)
        return [
            tuple(row)
            for row in conn.execute(
                text(
                    "SELECT CAST(patient_id AS text), starts_at, expires_at, "
                    "CAST(away_period_id AS text) FROM patient_clinicians "
                    "WHERE role = 'covering' ORDER BY patient_id"
                )
            ).all()
        ]


def _set_window(
    engine: Engine, schema: str, *, starts: datetime | None, expires: datetime | None
) -> None:
    with engine.begin() as conn:
        _scoped(conn, schema, _COVER)
        conn.execute(
            text(
                "UPDATE patient_clinicians SET starts_at = :s, expires_at = :e "
                "WHERE role = 'covering'"
            ),
            {"s": starts, "e": expires},
        )


def _audit(engine: Engine, schema: str, reader: str, action: str) -> list[dict]:
    """Audit rows with *action*, as *reader* is allowed to read them."""
    with engine.begin() as conn:
        _scoped(conn, schema, reader)
        rows = conn.execute(
            text(
                "SELECT user_id, CAST(patient_id AS text) AS patient_id, changes "
                "FROM audit_logs WHERE action = :a ORDER BY timestamp"
            ),
            {"a": action},
        ).mappings()
        return [dict(row) for row in rows]


# ---------------------------------------------------------------------------
# has_patient_access honours the window, as the database decides it
# ---------------------------------------------------------------------------


@pytest.mark.usefixtures("clean")
class TestTheWindow:
    def test_no_access_before_it_starts_inside_it_yes_after_it_no(
        self,
        client: TestClient,
        as_clinician: dict[str, str],
        engine: Engine,
        tenant_schema: str,
        seed: _Seed,
    ) -> None:
        period = _set_away(
            client, as_clinician, start=_today() + timedelta(days=10), end=_today() + timedelta(14)
        )
        _answer(client, as_clinician, period["id"], "accept")
        client_id = seed.away[0]
        now = datetime.now(UTC)

        # Booked ahead: the grant exists and gives nothing yet.
        assert len(_covering_grants(engine, tenant_schema)) == len(seed.away)
        assert not _has_access(engine, tenant_schema, _COVER, client_id)
        assert not _sees(engine, tenant_schema, _COVER, client_id)

        _set_window(
            engine, tenant_schema, starts=now - timedelta(hours=1), expires=now + timedelta(hours=1)
        )
        assert _has_access(engine, tenant_schema, _COVER, client_id)
        assert _sees(engine, tenant_schema, _COVER, client_id)

        _set_window(
            engine, tenant_schema, starts=now - timedelta(days=2), expires=now - timedelta(hours=1)
        )
        assert not _has_access(engine, tenant_schema, _COVER, client_id)
        assert not _sees(engine, tenant_schema, _COVER, client_id)

    def test_the_window_is_the_away_dates_on_the_clinicians_calendar(
        self,
        client: TestClient,
        as_clinician: dict[str, str],
        engine: Engine,
        tenant_schema: str,
    ) -> None:
        start = _today() + timedelta(days=3)
        period = _set_away(client, as_clinician, start=start, end=start + timedelta(days=4))
        _answer(client, as_clinician, period["id"], "accept")

        # No preference saved: the clinician's calendar is the default zone's.
        from zoneinfo import ZoneInfo  # noqa: PLC0415

        from app.models.user import UserPreferences  # noqa: PLC0415

        zone = ZoneInfo(UserPreferences().timezone)
        returns = date.fromisoformat(period["return_date"])
        for _pid, starts, expires, away_period_id in _covering_grants(engine, tenant_schema):
            assert starts == datetime.combine(start, datetime.min.time(), zone)
            assert expires == datetime.combine(returns, datetime.min.time(), zone)
            assert away_period_id == period["id"]


# ---------------------------------------------------------------------------
# Asking, accepting, declining, ending
# ---------------------------------------------------------------------------


@pytest.mark.usefixtures("clean")
class TestAcceptance:
    def test_naming_a_colleague_grants_nothing_until_they_accept(
        self,
        client: TestClient,
        as_clinician: dict[str, str],
        engine: Engine,
        tenant_schema: str,
        seed: _Seed,
    ) -> None:
        period = _away_now(client, as_clinician)
        assert period["coverage_status"] == "requested"

        assert _covering_grants(engine, tenant_schema) == []
        as_clinician["id"] = _COVER
        assert client.get(f"/api/patients/{seed.away[0]}").status_code == 404
        asked = client.get("/api/covering").json()["requests"]
        assert [(r["period_id"], r["status"], r["clinician_name"]) for r in asked] == [
            (period["id"], "requested", "Robin Avery")
        ]

        accepted = _answer(client, as_clinician, period["id"], "accept")

        assert accepted["status"] == "accepted"
        assert client.get(f"/api/patients/{seed.away[0]}").status_code == 200
        assert _sees(engine, tenant_schema, _COVER, seed.away[1])
        granted = [
            row
            for row in _audit(engine, tenant_schema, _COVER, "coverage_access_granted")
            if row["changes"]["away_period_id"] == period["id"]
        ]
        assert sorted(row["patient_id"] for row in granted) == sorted(seed.away)
        assert all(row["changes"]["starts_at"] and row["changes"]["expires_at"] for row in granted)
        assert [
            row["changes"]["client_count"]
            for row in _audit(engine, tenant_schema, _COVER, "coverage_accepted")
            if row["changes"]["away_period_id"] == period["id"]
        ] == [len(seed.away)]

    def test_a_decline_writes_the_decline_and_no_grant(
        self,
        client: TestClient,
        as_clinician: dict[str, str],
        engine: Engine,
        tenant_schema: str,
        seed: _Seed,
    ) -> None:
        period = _away_now(client, as_clinician)

        declined = _answer(client, as_clinician, period["id"], "decline")

        assert declined["status"] == "declined"
        assert _covering_grants(engine, tenant_schema) == []
        assert not _sees(engine, tenant_schema, _COVER, seed.away[0])
        declines = [
            row
            for row in _audit(engine, tenant_schema, _COVER, "coverage_declined")
            if row["changes"]["away_period_id"] == period["id"]
        ]
        assert len(declines) == 1
        assert declines[0]["patient_id"] is None

    def test_the_colleague_reaches_only_the_away_clinicians_clients(
        self,
        client: TestClient,
        as_clinician: dict[str, str],
        engine: Engine,
        tenant_schema: str,
        seed: _Seed,
    ) -> None:
        period = _away_now(client, as_clinician)
        _answer(client, as_clinician, period["id"], "accept")

        with engine.begin() as conn:
            _scoped(conn, tenant_schema, _COVER)
            visible = {
                str(row[0])
                for row in conn.execute(text("SELECT id FROM patients WHERE deleted_at IS NULL"))
            }
        assert visible == {*seed.away, seed.covers_own}
        assert not _has_access(engine, tenant_schema, _COVER, seed.thirds)
        as_clinician["id"] = _COVER
        assert client.get(f"/api/patients/{seed.thirds}").status_code == 404
        # And the third clinician gained nothing from somebody else's coverage.
        assert not _sees(engine, tenant_schema, _THIRD, seed.away[0])

    def test_only_the_named_colleague_can_answer(
        self,
        client: TestClient,
        as_clinician: dict[str, str],
        engine: Engine,
        tenant_schema: str,
    ) -> None:
        period = _away_now(client, as_clinician)

        for who in (_THIRD, _AWAY):
            as_clinician["id"] = who
            assert client.post(f"/api/covering/{period['id']}/accept").status_code == 404
        assert _covering_grants(engine, tenant_schema) == []

    def test_a_second_coverage_of_the_same_clinician_waits_for_the_first(
        self,
        client: TestClient,
        as_clinician: dict[str, str],
        engine: Engine,
        tenant_schema: str,
    ) -> None:
        first = _away_now(client, as_clinician, days=2)
        later = _set_away(
            client, as_clinician, start=_today() + timedelta(days=20), end=_today() + timedelta(23)
        )
        _answer(client, as_clinician, first["id"], "accept")

        as_clinician["id"] = _COVER
        refused = client.post(f"/api/covering/{later['id']}/accept")

        assert refused.status_code == 409
        assert {grant[3] for grant in _covering_grants(engine, tenant_schema)} == {first["id"]}


@pytest.mark.usefixtures("clean")
class TestEnding:
    def test_ending_early_ends_access_at_once(
        self,
        client: TestClient,
        as_clinician: dict[str, str],
        engine: Engine,
        tenant_schema: str,
        seed: _Seed,
    ) -> None:
        period = _away_now(client, as_clinician)
        _answer(client, as_clinician, period["id"], "accept")
        assert _sees(engine, tenant_schema, _COVER, seed.away[0])

        as_clinician["id"] = _AWAY
        assert client.post(f"/api/away-periods/{period['id']}/end").status_code == 200

        assert not _sees(engine, tenant_schema, _COVER, seed.away[0])
        assert not _has_access(engine, tenant_schema, _COVER, seed.away[1])
        as_clinician["id"] = _COVER
        assert client.get(f"/api/patients/{seed.away[0]}").status_code == 404
        ended = [
            row
            for row in _audit(engine, tenant_schema, _AWAY, "coverage_access_ended")
            if row["changes"]["away_period_id"] == period["id"]
        ]
        assert sorted(row["patient_id"] for row in ended) == sorted(seed.away)

    def test_new_dates_end_access_and_ask_again(
        self,
        client: TestClient,
        as_clinician: dict[str, str],
        engine: Engine,
        tenant_schema: str,
        seed: _Seed,
    ) -> None:
        period = _away_now(client, as_clinician)
        _answer(client, as_clinician, period["id"], "accept")

        as_clinician["id"] = _AWAY
        changed = client.patch(
            f"/api/away-periods/{period['id']}",
            json={
                "end_date": (_today() + timedelta(days=9)).isoformat(),
                "acknowledgment_text": _REVIEWED,
            },
        )

        assert changed.status_code == 200, changed.text
        assert changed.json()["coverage_status"] == "requested"
        assert not _sees(engine, tenant_schema, _COVER, seed.away[0])

    def test_withdrawing_after_accepting_ends_access(
        self,
        client: TestClient,
        as_clinician: dict[str, str],
        engine: Engine,
        tenant_schema: str,
        seed: _Seed,
    ) -> None:
        period = _away_now(client, as_clinician)
        _answer(client, as_clinician, period["id"], "accept")

        withdrawn = _answer(client, as_clinician, period["id"], "decline")

        assert withdrawn["status"] == "declined"
        assert not _sees(engine, tenant_schema, _COVER, seed.away[0])


@pytest.mark.usefixtures("clean")
class TestClientsAddedMeanwhile:
    def test_a_client_added_during_the_window_is_covered(
        self,
        client: TestClient,
        as_clinician: dict[str, str],
        engine: Engine,
        tenant_schema: str,
    ) -> None:
        period = _away_now(client, as_clinician)
        _answer(client, as_clinician, period["id"], "accept")

        as_clinician["id"] = _AWAY
        created = client.post("/api/patients", json={"first_name": "Newly", "last_name": "Added"})
        assert created.status_code == 201, created.text
        new_id = created.json()["id"]

        assert _sees(engine, tenant_schema, _COVER, new_id)
        assert not _sees(engine, tenant_schema, _THIRD, new_id)
        granted = _audit(engine, tenant_schema, _AWAY, "coverage_access_granted")
        assert [row["patient_id"] for row in granted if row["patient_id"] == new_id] == [new_id]

    def test_a_client_added_without_coverage_is_not(
        self,
        client: TestClient,
        as_clinician: dict[str, str],
        engine: Engine,
        tenant_schema: str,
    ) -> None:
        period = _away_now(client, as_clinician)
        assert period["coverage_status"] == "requested"

        as_clinician["id"] = _AWAY
        created = client.post("/api/patients", json={"first_name": "Not", "last_name": "Covered"})
        assert created.status_code == 201, created.text

        assert not _sees(engine, tenant_schema, _COVER, created.json()["id"])


# ---------------------------------------------------------------------------
# The audit trail, and what the colleague did
# ---------------------------------------------------------------------------


@pytest.mark.usefixtures("clean")
class TestCoverageActivity:
    def test_the_colleagues_reads_are_marked_and_listed_for_the_away_clinician_only(
        self,
        client: TestClient,
        as_clinician: dict[str, str],
        engine: Engine,
        tenant_schema: str,
        seed: _Seed,
    ) -> None:
        period = _away_now(client, as_clinician)
        _answer(client, as_clinician, period["id"], "accept")

        as_clinician["id"] = _COVER
        assert client.get(f"/api/patients/{seed.away[0]}").status_code == 200
        assert client.get(f"/api/patients/{seed.covers_own}").status_code == 200

        viewed = _audit(engine, tenant_schema, _COVER, "patient_viewed")
        marked = {row["patient_id"]: row["changes"] for row in viewed}
        assert marked[seed.away[0]]["grant_role"] == "covering"
        assert marked[seed.away[0]]["coverage_period_id"] == period["id"]
        assert "coverage_period_id" not in (marked[seed.covers_own] or {})

        as_clinician["id"] = _AWAY
        activity = client.get(f"/api/away-periods/{period['id']}/coverage-activity")
        assert activity.status_code == 200, activity.text
        body = activity.json()
        assert body["covering_name"] == "Sam Okafor"
        assert {(i["action"], i["patient_id"]) for i in body["items"]} == {
            ("patient_viewed", seed.away[0])
        }
        assert body["items"][0]["client_name"] == "Covered0 Client"

        # Nobody else reads those rows: not the third clinician, and not the
        # away clinician for a period that is somebody else's.
        assert not [
            row
            for row in _audit(engine, tenant_schema, _THIRD, "patient_viewed")
            if row["user_id"] == _COVER
        ]
        as_clinician["id"] = _THIRD
        assert client.get(f"/api/away-periods/{period['id']}/coverage-activity").status_code == 404
        assert _audit(engine, tenant_schema, _AWAY, "coverage_activity_viewed")

    def test_a_covered_period_that_is_over_stays_listed(
        self,
        client: TestClient,
        as_clinician: dict[str, str],
        engine: Engine,
        tenant_schema: str,
    ) -> None:
        period = _away_now(client, as_clinician)
        _answer(client, as_clinician, period["id"], "accept")
        as_clinician["id"] = _AWAY
        client.post(f"/api/away-periods/{period['id']}/end")

        listed = client.get("/api/away-periods").json()

        assert [p["id"] for p in listed["periods"]] == []
        assert [p["id"] for p in listed["recently_covered"]] == [period["id"]]
        assert _covering_grants(engine, tenant_schema)  # ended, kept as the record
