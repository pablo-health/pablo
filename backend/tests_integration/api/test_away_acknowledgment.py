# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Clinician away periods and the acknowledgment a client gets, on the real stack.

One practice with two clinicians — the one going away and a colleague who
may cover — and four clients: two whose primary clinician is the one going
away, one whose primary clinician is the colleague, and one with no primary
grant at all.

The acknowledgment is driven two ways. Through the service, on a session
armed as the client exactly as a portal request arms it, so the row policies
are the ones that ship: the client reads their clinician's period, finds
their primary clinician through the grant table they cannot see, and writes a
``practice`` message into their own thread. And through the portal routes
over HTTP, so the thread a client reads back is checked too.

What each test would catch: a second acknowledgment on one thread in one
period; none for a new period on the same thread; anything sent before the
period starts, after it ends or is ended, or with the acknowledgment off; a
client acknowledged on somebody else's dates; a body that is not the
reviewed text plus the safety lines, or that lacks 911 or 988; a refill line
that names anything but the return date or the covering colleague; and the
away routes letting dates change without the text being reviewed again,
overlapping, or naming somebody outside the practice to cover.

Run: ``make test-integration``.
"""

from __future__ import annotations

import os
import re
import uuid
from datetime import UTC, date, datetime, timedelta
from typing import TYPE_CHECKING

import pytest
from sqlalchemy import text

if TYPE_CHECKING:
    from collections.abc import Iterator

    from fastapi import FastAPI, Request
    from fastapi.testclient import TestClient
    from sqlalchemy.engine import Engine
    from sqlalchemy.orm import Session

_db_url = os.environ.get("DATABASE_URL", "")
pytestmark = pytest.mark.skipif(
    not _db_url or os.environ.get("DATABASE_BACKEND") != "postgres",
    reason=(
        "PostgreSQL not configured. Set DATABASE_URL and "
        "DATABASE_BACKEND=postgres; testcontainers should set both."
    ),
)

os.environ.setdefault("ENVIRONMENT", "development")

_CLINICIAN = "6d2f8a14-3b7c-4e95-a1d0-8c5e2f7b9a36"
_COLLEAGUE = "9b4e1c73-6a28-4f05-b3d9-2e7a5c8f1d40"
_OUTSIDER = "1f7c3a95-2d64-4b8e-9c10-5a3e8d6b2f71"
_CLINICIAN_EMAIL = "away-ack-clinician@example.com"
_COLLEAGUE_EMAIL = "away-ack-colleague@example.com"
_CLINICIAN_NAME = "Robin Avery"
_COLLEAGUE_NAME = "Sam Okafor"

_TOKEN_A = "away-ack-client-a"  # noqa: S105 - test credential
_TOKEN_B = "away-ack-client-b"  # noqa: S105 - test credential

#: Noon UTC is the same calendar day in the clinician's default zone.
_DAY = date(2026, 3, 10)
_NOON = datetime(2026, 3, 10, 12, 0, tzinfo=UTC)
_REVIEWED = "Thanks for writing. I'm away and back on Monday, March 16."


class _Seed:
    def __init__(self) -> None:
        self.mine = [str(uuid.uuid4()) for _ in range(2)]
        self.colleagues = str(uuid.uuid4())
        self.ungranted = str(uuid.uuid4())


@pytest.fixture(scope="module")
def tenant_schema(engine: Engine) -> Iterator[str]:
    """A practice with two clinicians, both mapped to it as members."""
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
                name="Away Acknowledgment",
                schema_name=schema,
                owner_email=_CLINICIAN_EMAIL,
                owner_user_id=_CLINICIAN,
                created_at=now,
            )
        )
        for user_id, email, name in (
            (_CLINICIAN, _CLINICIAN_EMAIL, _CLINICIAN_NAME),
            (_COLLEAGUE, _COLLEAGUE_EMAIL, _COLLEAGUE_NAME),
            (_OUTSIDER, "away-ack-outsider@example.com", "Outside Practice"),
        ):
            session.add(PlatformUserRow(id=user_id, email=email, name=name, created_at=now))
        for email in (_CLINICIAN_EMAIL, _COLLEAGUE_EMAIL):
            session.add(
                EmailTenantMappingRow(
                    email=email, tenant_id=practice_id, practice_id=practice_id, created_at=now
                )
            )
        session.commit()
    yield schema
    with OrmSession(engine) as session:
        for email in (_CLINICIAN_EMAIL, _COLLEAGUE_EMAIL):
            session.query(EmailTenantMappingRow).filter_by(email=email).delete()
        for user_id in (_CLINICIAN, _COLLEAGUE, _OUTSIDER):
            session.query(PlatformUserRow).filter_by(id=user_id).delete()
        session.query(PracticeRow).filter_by(id=practice_id).delete()
        session.commit()
    with engine.connect() as conn:
        conn.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        conn.commit()


@pytest.fixture(scope="module")
def seed(engine: Engine, tenant_schema: str) -> _Seed:
    s = _Seed()
    grants = [(pid, _CLINICIAN, "primary") for pid in s.mine]
    grants += [(s.colleagues, _COLLEAGUE, "primary"), (s.ungranted, _CLINICIAN, "co_treating")]
    with engine.begin() as conn:
        conn.execute(text(f"SET search_path = {tenant_schema}, platform, public"))
        for i, (pid, owner, role) in enumerate(grants):
            conn.execute(text("SELECT set_config('app.current_user_id', :u, true)"), {"u": owner})
            conn.execute(
                text(
                    "INSERT INTO patients (id, first_name, last_name, first_name_lower, "
                    "last_name_lower, status, session_count, created_at, updated_at) "
                    "VALUES (CAST(:pid AS uuid), :first, 'Writer', lower(:first), 'writer', "
                    "'active', 0, now(), now())"
                ),
                {"pid": pid, "first": f"Client{i}"},
            )
            conn.execute(
                text(
                    "INSERT INTO patient_clinicians (patient_id, user_id, granted_by, role) "
                    "VALUES (CAST(:pid AS uuid), :u, :u, :role)"
                ),
                {"pid": pid, "u": owner, "role": role},
            )
    return s


@pytest.fixture
def periods(engine: Engine, tenant_schema: str) -> Iterator[None]:
    """No away periods or messages carry over from one test to the next."""
    yield
    with engine.begin() as conn:
        conn.execute(text(f"SET search_path = {tenant_schema}, platform, public"))
        conn.execute(text("DELETE FROM patient_message_threads"))
        conn.execute(text("DELETE FROM clinician_away_periods"))


def _period(  # noqa: PLR0913 — one row, every column a test may vary
    engine: Engine,
    schema: str,
    *,
    start: date,
    end: date,
    returns: date | None = None,
    covering: str | None = None,
    coverage: str = "accepted",
    enabled: bool = True,
    ended: bool = False,
    user_id: str = _CLINICIAN,
) -> str:
    period_id = str(uuid.uuid4())
    with engine.begin() as conn:
        conn.execute(text(f"SET search_path = {schema}, platform, public"))
        conn.execute(
            text(
                "INSERT INTO clinician_away_periods (id, user_id, start_date, end_date, "
                "return_date, covering_user_id, coverage_status, acknowledgment_enabled, "
                "acknowledgment_text, ended_at, created_at, updated_at) VALUES "
                "(CAST(:id AS uuid), CAST(:u AS uuid), :start, :end, :returns, "
                "CAST(:cover AS uuid), :coverage, :enabled, :body, :ended, now(), now())"
            ),
            {
                "id": period_id,
                "u": user_id,
                "start": start,
                "end": end,
                "returns": returns or end + timedelta(days=1),
                "cover": covering,
                "coverage": None if covering is None else coverage,
                "enabled": enabled,
                "body": _REVIEWED,
                "ended": datetime.now(UTC) if ended else None,
            },
        )
    return period_id


def _thread(engine: Engine, schema: str, patient_id: str) -> str:
    thread_id = str(uuid.uuid4())
    with engine.begin() as conn:
        conn.execute(text(f"SET search_path = {schema}, platform, public"))
        conn.execute(
            text("SELECT set_config('app.current_patient_id', :p, true)"), {"p": patient_id}
        )
        conn.execute(
            text(
                "INSERT INTO patient_message_threads (id, patient_id, status, created_at, "
                "last_message_at) VALUES (CAST(:t AS uuid), CAST(:p AS uuid), 'open', "
                ":at, :at)"
            ),
            {"t": thread_id, "p": patient_id, "at": _NOON},
        )
    return thread_id


def _as_client(schema: str, patient_id: str) -> Session:
    """A session armed the way ``get_patient_context`` arms a portal request."""
    from app.db import arm_current_patient_id, create_standalone_session  # noqa: PLC0415

    session = create_standalone_session(schema)
    arm_current_patient_id(session, patient_id)
    return session


def _acknowledge(schema: str, patient_id: str, thread_id: str, now: datetime = _NOON) -> str | None:
    """Run the acknowledgment as the client; the sent body, or ``None``."""
    from app.away.service import PortalAwayAcknowledger  # noqa: PLC0415

    session = _as_client(schema, patient_id)
    try:
        sent = PortalAwayAcknowledger(session).acknowledge_message(
            practice_schema=schema, thread_id=thread_id, patient_id=patient_id, now=now
        )
        session.commit()
    finally:
        session.close()
    return None if sent is None else sent.body


def _refill(schema: str, patient_id: str, now: datetime = _NOON) -> str | None:
    from app.away.service import PortalAwayAcknowledger  # noqa: PLC0415

    session = _as_client(schema, patient_id)
    try:
        return PortalAwayAcknowledger(session).refill_acknowledgment(
            practice_schema=schema, patient_id=patient_id, now=now
        )
    finally:
        session.close()


def _practice_messages(engine: Engine, schema: str, thread_id: str) -> list[tuple[str, str]]:
    with engine.begin() as conn:
        conn.execute(text(f"SET search_path = {schema}, platform, public"))
        # Read as a clinician with a grant on every client here but the colleague's.
        conn.execute(text("SELECT set_config('app.current_user_id', :u, true)"), {"u": _CLINICIAN})
        rows = conn.execute(
            text(
                "SELECT body, CAST(away_period_id AS text) FROM patient_messages "
                "WHERE thread_id = CAST(:t AS uuid) AND sender = 'practice'"
            ),
            {"t": thread_id},
        ).all()
    return [(r[0], r[1]) for r in rows]


# ---------------------------------------------------------------------------
# The acknowledgment, as the client
# ---------------------------------------------------------------------------


@pytest.mark.usefixtures("periods")
class TestPortalAcknowledgment:
    def test_one_acknowledgment_per_thread_per_period(
        self, engine: Engine, tenant_schema: str, seed: _Seed
    ) -> None:
        period = _period(engine, tenant_schema, start=_DAY, end=_DAY + timedelta(days=5))
        thread = _thread(engine, tenant_schema, seed.mine[0])

        assert _acknowledge(tenant_schema, seed.mine[0], thread) is not None
        assert _acknowledge(tenant_schema, seed.mine[0], thread) is None
        assert _acknowledge(tenant_schema, seed.mine[0], thread, _NOON + timedelta(days=2)) is None

        sent = _practice_messages(engine, tenant_schema, thread)
        assert [away_id for _body, away_id in sent] == [period]

    def test_each_thread_and_each_period_gets_its_own(
        self, engine: Engine, tenant_schema: str, seed: _Seed
    ) -> None:
        _period(engine, tenant_schema, start=_DAY, end=_DAY, ended=True)
        _period(engine, tenant_schema, start=_DAY - timedelta(days=1), end=_DAY + timedelta(days=1))
        first = _thread(engine, tenant_schema, seed.mine[0])
        second = _thread(engine, tenant_schema, seed.mine[1])

        assert _acknowledge(tenant_schema, seed.mine[0], first) is not None
        assert _acknowledge(tenant_schema, seed.mine[1], second) is not None

        later = _period(
            engine, tenant_schema, start=_DAY + timedelta(days=20), end=_DAY + timedelta(days=22)
        )
        in_later = _NOON + timedelta(days=21)
        assert _acknowledge(tenant_schema, seed.mine[0], first, in_later) is not None
        assert [p for _b, p in _practice_messages(engine, tenant_schema, first)][-1] == later
        assert len(_practice_messages(engine, tenant_schema, first)) == 2

    def test_the_body_is_the_reviewed_text_and_the_safety_lines_and_nothing_else(
        self, engine: Engine, tenant_schema: str, seed: _Seed
    ) -> None:
        from app.away.text import SAFETY_LINES  # noqa: PLC0415

        _period(engine, tenant_schema, start=_DAY, end=_DAY + timedelta(days=5))
        body = _acknowledge(
            tenant_schema, seed.mine[0], _thread(engine, tenant_schema, seed.mine[0])
        )

        assert body == f"{_REVIEWED}\n\n{SAFETY_LINES}"
        assert "If this is an emergency, call 911." in body
        assert "988" in body

    @pytest.mark.parametrize(
        ("start_offset", "end_offset", "enabled", "ended"),
        [
            pytest.param(1, 5, True, False, id="not-started-yet"),
            pytest.param(-5, -1, True, False, id="already-over"),
            pytest.param(-1, 5, True, True, id="ended-early"),
            pytest.param(-1, 5, False, False, id="turned-off"),
        ],
    )
    def test_nothing_is_sent_outside_the_period_or_when_off(  # noqa: PLR0913
        self,
        engine: Engine,
        tenant_schema: str,
        seed: _Seed,
        start_offset: int,
        end_offset: int,
        enabled: bool,
        ended: bool,
    ) -> None:
        _period(
            engine,
            tenant_schema,
            start=_DAY + timedelta(days=start_offset),
            end=_DAY + timedelta(days=end_offset),
            enabled=enabled,
            ended=ended,
        )
        thread = _thread(engine, tenant_schema, seed.mine[0])

        assert _acknowledge(tenant_schema, seed.mine[0], thread) is None
        assert _practice_messages(engine, tenant_schema, thread) == []

    def test_only_the_clients_primary_clinicians_dates_count(
        self, engine: Engine, tenant_schema: str, seed: _Seed
    ) -> None:
        _period(engine, tenant_schema, start=_DAY, end=_DAY + timedelta(days=5))

        colleagues = _thread(engine, tenant_schema, seed.colleagues)
        ungranted = _thread(engine, tenant_schema, seed.ungranted)
        assert _acknowledge(tenant_schema, seed.colleagues, colleagues) is None
        assert _acknowledge(tenant_schema, seed.ungranted, ungranted) is None

        _period(engine, tenant_schema, start=_DAY, end=_DAY + timedelta(days=5), user_id=_COLLEAGUE)
        assert _acknowledge(tenant_schema, seed.colleagues, colleagues) is not None


@pytest.mark.usefixtures("periods")
class TestRefillAcknowledgment:
    def test_names_the_return_date_only(
        self, engine: Engine, tenant_schema: str, seed: _Seed
    ) -> None:
        _period(
            engine,
            tenant_schema,
            start=_DAY,
            end=_DAY + timedelta(days=3),
            returns=date(2026, 3, 16),
        )

        line = _refill(tenant_schema, seed.mine[0])

        assert line is not None
        assert line.startswith("Your request is in. It will be reviewed by Monday, March 16.\n\n")
        assert "911" in line
        assert "988" in line
        assert not re.search(r"\d{1,2}:\d{2}|\b(am|pm)\b", line, re.IGNORECASE)

    def test_names_the_covering_colleague_when_there_is_one(
        self, engine: Engine, tenant_schema: str, seed: _Seed
    ) -> None:
        _period(
            engine,
            tenant_schema,
            start=_DAY,
            end=_DAY + timedelta(days=3),
            returns=date(2026, 3, 16),
            covering=_COLLEAGUE,
        )

        line = _refill(tenant_schema, seed.mine[0])

        assert line is not None
        assert line.startswith(
            f"Your request is in. It will be reviewed by Monday, March 16 or by {_COLLEAGUE_NAME}."
        )

    @pytest.mark.parametrize("coverage", ["requested", "declined"])
    def test_a_colleague_who_has_not_accepted_is_not_named(
        self, engine: Engine, tenant_schema: str, seed: _Seed, coverage: str
    ) -> None:
        _period(
            engine,
            tenant_schema,
            start=_DAY,
            end=_DAY + timedelta(days=3),
            returns=date(2026, 3, 16),
            covering=_COLLEAGUE,
            coverage=coverage,
        )

        line = _refill(tenant_schema, seed.mine[0])

        assert line is not None
        assert line.startswith("Your request is in. It will be reviewed by Monday, March 16.\n\n")
        assert _COLLEAGUE_NAME not in line

    def test_nothing_when_off_or_not_away(
        self, engine: Engine, tenant_schema: str, seed: _Seed
    ) -> None:
        assert _refill(tenant_schema, seed.mine[0]) is None
        _period(engine, tenant_schema, start=_DAY, end=_DAY + timedelta(days=3), enabled=False)
        assert _refill(tenant_schema, seed.mine[0]) is None


# ---------------------------------------------------------------------------
# Over HTTP: the clinician's away routes and the client's portal thread
# ---------------------------------------------------------------------------


@pytest.fixture
def client(
    fastapi_app: FastAPI,
    tenant_schema: str,
    seed: _Seed,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[TestClient]:
    """The real route stack: the clinician's routes, and two clients' portal routes."""
    from app.auth.patient_context import (  # noqa: PLC0415
        AuthStrength,
        PatientContext,
        PatientCredential,
        PatientResolverRegistry,
        get_patient_resolver_registry,
    )
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
    from app.portal import module_gate  # noqa: PLC0415
    from app.portal.portal_settings import NOT_OFFERED  # noqa: PLC0415
    from app.rate_limit import reset_patient_message_send_limiter  # noqa: PLC0415
    from fastapi.testclient import TestClient  # noqa: PLC0415

    user = User(
        id=_CLINICIAN,
        email=_CLINICIAN_EMAIL,
        name=_CLINICIAN_NAME,
        created_at=datetime(2024, 1, 1, tzinfo=UTC),
        baa_accepted_at=datetime(2024, 1, 1, tzinfo=UTC),
        baa_version="2024-01-01",
    )
    identity = VerifiedIdentity(
        provider="test",
        subject_id=_CLINICIAN,
        email=_CLINICIAN_EMAIL,
        mfa_satisfied=True,
        claims={},
    )

    def _stash_identity(request: Request) -> None:
        # Clinician routes only: a patient route refuses a clinician credential.
        if not request.url.path.startswith("/api/patient/"):
            request.state.verified_identity = identity

    monkeypatch.setattr("app.db.middleware._verify_and_stash_clinician_identity", _stash_identity)
    monkeypatch.setattr(module_gate, "portal_settings_for_schema", lambda _schema: NOT_OFFERED)

    def _armed_user() -> User:
        arm_current_user_id(get_db_session(), _CLINICIAN)
        return user

    def _tenant_context() -> TenantContext:
        arm_current_user_id(get_db_session(), _CLINICIAN)
        return TenantContext(
            user_id=_CLINICIAN, practice_id="test-tenant", practice_schema=tenant_schema
        )

    class _Clients:
        credential_kind = "bearer"

        def resolve(self, credential: PatientCredential) -> PatientContext | None:
            patient_id = {_TOKEN_A: seed.mine[0], _TOKEN_B: seed.colleagues}.get(credential.value)
            if patient_id is None:
                return None
            return PatientContext(
                patient_id=patient_id,
                practice_schema=tenant_schema,
                credential_kind="bearer",
                auth_strength=AuthStrength.STEPPED_UP,
            )

    registry = PatientResolverRegistry()
    registry.register(_Clients())
    reset_patient_message_send_limiter()

    fastapi_app.dependency_overrides[get_patient_resolver_registry] = lambda: registry
    fastapi_app.dependency_overrides[get_current_user_id] = lambda: _CLINICIAN
    fastapi_app.dependency_overrides[get_current_user] = _armed_user
    fastapi_app.dependency_overrides[get_current_user_no_mfa] = _armed_user
    fastapi_app.dependency_overrides[require_active_subscription] = _armed_user
    fastapi_app.dependency_overrides[require_baa_acceptance] = _armed_user
    fastapi_app.dependency_overrides[get_tenant_context] = _tenant_context
    try:
        yield TestClient(fastapi_app)
    finally:
        fastapi_app.dependency_overrides.clear()


def _today() -> date:
    """Today, with a day of slack either side for the clinician's zone."""
    return datetime.now(UTC).date()


def _set_away(client: TestClient, **overrides: object) -> dict:
    body: dict[str, object] = {
        "start_date": (_today() - timedelta(days=1)).isoformat(),
        "end_date": (_today() + timedelta(days=5)).isoformat(),
        "acknowledgment_text": _REVIEWED,
    }
    body |= overrides
    created = client.post("/api/away-periods", json=body)
    assert created.status_code == 201, created.text
    return created.json()


@pytest.mark.usefixtures("periods")
class TestAwayRoutes:
    def test_setting_dates_turns_the_acknowledgment_on_and_returns_the_day_after(
        self, client: TestClient
    ) -> None:
        period = _set_away(client)

        assert period["acknowledgment_enabled"] is True
        assert period["active"] is True
        assert period["return_date"] == (_today() + timedelta(days=6)).isoformat()
        assert "911" in period["safety_lines"]
        assert "988" in period["safety_lines"]

        listed = client.get("/api/away-periods").json()
        assert [p["id"] for p in listed["periods"]] == [period["id"]]
        assert listed["covering_options"] == [{"user_id": _COLLEAGUE, "name": _COLLEAGUE_NAME}]

    def test_the_preview_shows_the_return_date_and_the_covering_colleague(
        self, client: TestClient
    ) -> None:
        preview = client.post(
            "/api/away-periods/preview",
            json={
                "start_date": "2026-03-10",
                "end_date": "2026-03-13",
                "return_date": "2026-03-16",
                "covering_user_id": _COLLEAGUE,
            },
        )

        assert preview.status_code == 200, preview.text
        assert preview.json()["acknowledgment_text"] == (
            f"Thanks for your message. {_CLINICIAN_NAME} is away and back on Monday, March 16. "
            f"{_COLLEAGUE_NAME} is covering in the meantime."
        )

    def test_new_dates_need_the_text_reviewed_again(self, client: TestClient) -> None:
        period = _set_away(client)
        later = (_today() + timedelta(days=7)).isoformat()

        refused = client.patch(f"/api/away-periods/{period['id']}", json={"end_date": later})
        assert refused.status_code == 422
        assert refused.json()["error"]["code"] == "REVIEW_ACKNOWLEDGMENT_TEXT"

        changed = client.patch(
            f"/api/away-periods/{period['id']}",
            json={"end_date": later, "acknowledgment_text": "Back a little later now."},
        )
        assert changed.status_code == 200, changed.text
        assert changed.json()["return_date"] == (_today() + timedelta(days=8)).isoformat()
        assert changed.json()["acknowledgment_text"] == "Back a little later now."

        switched_off = client.patch(
            f"/api/away-periods/{period['id']}", json={"acknowledgment_enabled": False}
        )
        assert switched_off.json()["acknowledgment_enabled"] is False

    def test_overlapping_dates_and_an_outside_colleague_are_refused(
        self, client: TestClient
    ) -> None:
        _set_away(client)

        overlap = client.post(
            "/api/away-periods",
            json={
                "start_date": _today().isoformat(),
                "end_date": (_today() + timedelta(days=2)).isoformat(),
                "acknowledgment_text": _REVIEWED,
            },
        )
        assert overlap.status_code == 409

        outsider = client.post(
            "/api/away-periods",
            json={
                "start_date": (_today() + timedelta(days=30)).isoformat(),
                "end_date": (_today() + timedelta(days=31)).isoformat(),
                "covering_user_id": _OUTSIDER,
                "acknowledgment_text": _REVIEWED,
            },
        )
        assert outsider.status_code == 422

    def test_ending_a_period_stops_the_acknowledgment(
        self, client: TestClient, engine: Engine, tenant_schema: str
    ) -> None:
        period = _set_away(client)

        ended = client.post(f"/api/away-periods/{period['id']}/end")
        assert ended.status_code == 200, ended.text
        assert ended.json()["active"] is False
        assert client.get("/api/away-periods").json()["periods"] == []

        started = client.post(
            "/api/patient/messages/threads",
            json={"subject": None, "body": "Are you around this week?"},
            headers={"Authorization": f"Bearer {_TOKEN_A}"},
        )
        assert [m["sender"] for m in started.json()["messages"]] == ["patient"]
        assert _practice_messages(engine, tenant_schema, started.json()["id"]) == []

    def test_a_client_writing_in_sees_one_acknowledgment(self, client: TestClient) -> None:
        _set_away(client)
        headers = {"Authorization": f"Bearer {_TOKEN_A}"}

        started = client.post(
            "/api/patient/messages/threads",
            json={"subject": None, "body": "Can we talk about next week?"},
            headers=headers,
        )
        assert started.status_code == 201, started.text
        thread = started.json()
        assert [m["sender"] for m in thread["messages"]] == ["patient", "practice"]
        assert thread["messages"][1]["body"].startswith(_REVIEWED)

        again = client.post(
            f"/api/patient/messages/threads/{thread['id']}/messages",
            json={"body": "One more thing."},
            headers=headers,
        )
        assert again.status_code == 201, again.text
        read_back = client.get(f"/api/patient/messages/threads/{thread['id']}", headers=headers)
        assert [m["sender"] for m in read_back.json()["messages"]] == [
            "patient",
            "practice",
            "patient",
        ]

        others = client.post(
            "/api/patient/messages/threads",
            json={"subject": None, "body": "Hello"},
            headers={"Authorization": f"Bearer {_TOKEN_B}"},
        )
        assert [m["sender"] for m in others.json()["messages"]] == ["patient"]
