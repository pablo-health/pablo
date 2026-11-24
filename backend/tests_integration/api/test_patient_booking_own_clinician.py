# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""A client books with their own clinician, in a practice of two.

Owner O works mornings and clinician B works afternoons, so which diary a slot
came from shows in its hour. B's clients book into B's afternoon, O's into
O's morning, two of B's clients cannot take the same hour, and a client whose
primary grant has ended is refused with ``NO_CLINICIAN`` rather than handed to
the owner.

Requires ``DATABASE_URL`` + ``DATABASE_BACKEND=postgres``.
"""

from __future__ import annotations

import contextvars
import os
import uuid
from datetime import UTC, date, datetime, time, timedelta
from typing import TYPE_CHECKING, Any
from zoneinfo import ZoneInfo

import pytest
from app.auth.patient_context import AuthStrength, PatientContext, get_patient_context
from app.auth.route_access import subscription_exempt
from app.db import (
    _current_patient_id,
    _current_tenant_schema,
    _current_user_id,
    arm_current_patient_id,
    arm_current_user_id,
    get_db_session,
    set_tenant_schema,
)
from app.db.platform_models import EmailTenantMappingRow, PlatformUserRow, PracticeRow
from app.db.provisioning import create_practice_schema
from app.models.user import UserPreferences
from app.repositories.postgres.availability_rule import PostgresAvailabilityRuleRepository
from app.repositories.postgres.user import PostgresUserRepository
from app.scheduling_engine.models.availability import AvailabilityRule, EnforcementLevel, RuleType
from app.scheduling_engine.services.scheduling_policy import update_policy
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session as OrmSession

from tests_integration.database.patient_rows import seed_patient_row

if TYPE_CHECKING:
    from collections.abc import Iterator

    from fastapi import FastAPI
    from sqlalchemy.engine import Engine

pytestmark = pytest.mark.skipif(
    not os.environ.get("DATABASE_URL") or os.environ.get("DATABASE_BACKEND") != "postgres",
    reason="PostgreSQL not configured. Set DATABASE_URL and DATABASE_BACKEND=postgres.",
)

_SUFFIX = uuid.uuid4().hex[:8]
_SCHEMA = f"practice_test_own_{_SUFFIX}"
_PRACTICE_ID = f"practice-own-{_SUFFIX}"
_OWNER = str(uuid.uuid4())
_COLLEAGUE = str(uuid.uuid4())
_COLLEAGUE_EMAIL = f"colleague-{_SUFFIX}@example.test"
_TZ = ZoneInfo("America/New_York")
#: Local working hours per clinician, end exclusive, and the type each opens.
_HOURS = {_OWNER: (9, 12), _COLLEAGUE: (13, 17)}
_TYPES = {_OWNER: "Morning session", _COLLEAGUE: "Afternoon session"}


def _day() -> date:
    """A Monday two weeks out, in the clinicians' zone."""
    day = (datetime.now(UTC) + timedelta(days=14)).astimezone(_TZ).date()
    return day + timedelta(days=(7 - day.weekday()) % 7)


def _at(hour: int) -> datetime:
    return datetime.combine(_day(), time(hour), tzinfo=_TZ).astimezone(UTC)


@pytest.fixture(scope="module", autouse=True)
def _arming_here_stays_here() -> Iterator[None]:
    """Hand back the request ContextVars the seeding below sets in this thread."""

    tokens = [
        (var, var.set(var.get()))
        for var in (_current_user_id, _current_patient_id, _current_tenant_schema)
    ]
    yield
    for var, token in reversed(tokens):
        var.reset(token)


def _as(engine: Engine, user_id: str) -> OrmSession:
    session = OrmSession(bind=engine)
    set_tenant_schema(session, _SCHEMA)
    arm_current_user_id(session, user_id)
    return session


def _open_diary(engine: Engine, clinician: str) -> None:
    """The clinician's zone, their hours on the target Monday, and a bookable type."""
    start, end = _HOURS[clinician]
    now = datetime.now(UTC)
    with _as(engine, clinician) as s:
        PostgresUserRepository(s).save_preferences(
            clinician, UserPreferences(timezone="America/New_York")
        )
        rules = PostgresAvailabilityRuleRepository(s)
        for rule_type, params in (
            (
                RuleType.WORKING_HOURS,
                {"day_of_week": 0, "start": f"{start:02d}:00", "end": f"{end:02d}:00"},
            ),
            (RuleType.SESSION_DEFAULTS, {"alignment": "hour"}),
        ):
            rules.create(
                AvailabilityRule(
                    id=str(uuid.uuid4()),
                    user_id=clinician,
                    rule_type=rule_type.value,
                    enforcement=EnforcementLevel.SOFT.value,
                    params=params,
                    created_at=now,
                    updated_at=now,
                )
            )
        s.execute(
            text(
                "INSERT INTO appointment_types (id, user_id, name, duration_minutes, "
                "self_bookable, created_at, updated_at) VALUES "
                "(gen_random_uuid(), CAST(:u AS uuid), :n, 50, true, now(), now())"
            ),
            {"u": clinician, "n": _TYPES[clinician]},
        )
        s.commit()


@pytest.fixture(scope="module")
def practice(engine: Engine) -> Iterator[dict[str, str]]:
    """O owns the practice; B is a member. Each has clients; one client's grant has ended."""
    with engine.connect() as conn:
        conn.execute(text("SET search_path = practice, platform, public"))
        conn.commit()
    create_practice_schema(engine, _SCHEMA)

    now = datetime.now(UTC)
    with OrmSession(bind=engine) as s:
        s.add(
            PlatformUserRow(
                id=_OWNER, email=f"{_PRACTICE_ID}@example.test", name="Owner", created_at=now
            )
        )
        s.add(
            PlatformUserRow(id=_COLLEAGUE, email=_COLLEAGUE_EMAIL, name="Colleague", created_at=now)
        )
        s.add(
            PracticeRow(
                id=_PRACTICE_ID,
                name="Two Clinician Practice",
                schema_name=_SCHEMA,
                owner_email=f"{_PRACTICE_ID}@example.test",
                owner_user_id=_OWNER,
                created_at=now,
            )
        )
        s.add(
            EmailTenantMappingRow(
                email=_COLLEAGUE_EMAIL,
                tenant_id=_PRACTICE_ID,
                practice_id=_PRACTICE_ID,
                created_at=now,
            )
        )
        s.commit()

    _open_diary(engine, _OWNER)
    _open_diary(engine, _COLLEAGUE)
    with _as(engine, _OWNER) as s:
        update_policy(s, {"self_book_existing": True, "self_book_mode": "auto"})
        o_client = seed_patient_row(s, _OWNER)
        lapsed = seed_patient_row(s, _OWNER)
        s.execute(
            text(
                "UPDATE patient_clinicians SET expires_at = now() - interval '1 day' "
                "WHERE patient_id = CAST(:p AS uuid)"
            ),
            {"p": lapsed},
        )
        s.commit()
    with _as(engine, _COLLEAGUE) as s:
        b_client, b_other = seed_patient_row(s, _COLLEAGUE), seed_patient_row(s, _COLLEAGUE)
        s.commit()

    yield {"o": o_client, "b": b_client, "b2": b_other, "lapsed": lapsed}

    with engine.begin() as conn:
        conn.execute(text(f'DROP SCHEMA IF EXISTS "{_SCHEMA}" CASCADE'))
        conn.execute(
            text("DELETE FROM platform.email_tenant_mappings WHERE practice_id = :i"),
            {"i": _PRACTICE_ID},
        )
        conn.execute(text("DELETE FROM platform.practices WHERE id = :i"), {"i": _PRACTICE_ID})
        conn.execute(
            text("DELETE FROM platform.users WHERE id IN (CAST(:a AS uuid), CAST(:b AS uuid))"),
            {"a": _OWNER, "b": _COLLEAGUE},
        )


@pytest.fixture
def as_client(fastapi_app: FastAPI) -> Iterator[Any]:
    """``as_client(patient_id)``: a TestClient whose stepped-up patient principal is that id."""
    current: dict[str, str] = {}

    def _context() -> PatientContext:
        context = PatientContext(
            patient_id=current["id"],
            practice_schema=_SCHEMA,
            credential_kind="bearer",
            auth_strength=AuthStrength.STEPPED_UP,
        )
        session = get_db_session()
        set_tenant_schema(session, _SCHEMA)
        arm_current_patient_id(session, context.patient_id)
        return context

    class _FreshContextClient(TestClient):
        """Each request starts from an empty context, as it does under a server."""

        def request(self, *args: Any, **kwargs: Any) -> Any:  # type: ignore[override]
            return contextvars.Context().run(super().request, *args, **kwargs)

    fastapi_app.dependency_overrides[get_patient_context] = _context
    fastapi_app.dependency_overrides[subscription_exempt] = lambda: None
    client = _FreshContextClient(
        fastapi_app, base_url="https://testserver", raise_server_exceptions=False
    )

    def make(patient_id: str) -> TestClient:
        current["id"] = patient_id
        return client

    yield make
    fastapi_app.dependency_overrides.clear()


def _offered_hours(client: TestClient) -> list[int]:
    response = client.get("/api/patient/booking/slots", params={"date": _day().isoformat()})
    assert response.status_code == 200, response.text
    return [
        datetime.fromisoformat(slot["start_at"]).astimezone(_TZ).hour
        for slot in response.json()["data"]
    ]


def _book(client: TestClient, kind: str, hour: int) -> Any:
    return client.post(
        "/api/patient/booking", json={"session_type": kind, "start_at": _at(hour).isoformat()}
    )


def _diary(engine: Engine, clinician: str) -> list[str]:
    """Ids of the appointments in *clinician*'s diary, read as that clinician."""
    with _as(engine, clinician) as s:
        rows = s.execute(
            text("SELECT id FROM appointments WHERE user_id = CAST(:u AS uuid)"), {"u": clinician}
        )
        return [str(row) for row in rows.scalars()]


def _clear_appointments(engine: Engine) -> None:
    for clinician in _HOURS:
        with _as(engine, clinician) as s:
            s.execute(
                text("DELETE FROM appointments WHERE user_id = CAST(:u AS uuid)"), {"u": clinician}
            )
            s.commit()


def test_a_colleagues_client_books_into_the_colleagues_diary(
    engine: Engine, practice: dict[str, str], as_client: Any
) -> None:
    _clear_appointments(engine)
    client = as_client(practice["b"])

    assert _offered_hours(client) == [13, 14, 15, 16]
    options = client.get("/api/patient/booking/options").json()
    assert [t["name"] for t in options["session_types"]] == [_TYPES[_COLLEAGUE]]

    booked = _book(client, _TYPES[_COLLEAGUE], 14)
    assert booked.status_code == 201, booked.text
    assert _diary(engine, _COLLEAGUE) == [booked.json()["id"]]
    assert _diary(engine, _OWNER) == []


def test_the_owners_client_still_books_with_the_owner(
    engine: Engine, practice: dict[str, str], as_client: Any
) -> None:
    _clear_appointments(engine)
    client = as_client(practice["o"])

    assert _offered_hours(client) == [9, 10, 11]
    booked = _book(client, _TYPES[_OWNER], 10)
    assert booked.status_code == 201, booked.text
    assert _diary(engine, _OWNER) == [booked.json()["id"]]
    assert _diary(engine, _COLLEAGUE) == []


def test_two_of_the_colleagues_clients_cannot_take_one_hour(
    engine: Engine, practice: dict[str, str], as_client: Any
) -> None:
    _clear_appointments(engine)
    assert _book(as_client(practice["b"]), _TYPES[_COLLEAGUE], 14).status_code == 201

    other = as_client(practice["b2"])
    assert _offered_hours(other) == [13, 15, 16]
    taken = _book(other, _TYPES[_COLLEAGUE], 14)
    assert taken.status_code == 409, taken.text
    assert taken.json()["detail"]["error"]["code"] == "SLOT_TAKEN"


def test_a_client_with_no_live_clinician_is_refused_not_given_the_owner(
    engine: Engine, practice: dict[str, str], as_client: Any
) -> None:
    _clear_appointments(engine)
    client = as_client(practice["lapsed"])

    for response in (
        _book(client, _TYPES[_OWNER], 10),
        client.get("/api/patient/booking/slots", params={"date": _day().isoformat()}),
    ):
        assert response.status_code == 409, response.text
        assert response.json()["detail"]["error"]["code"] == "NO_CLINICIAN"
    # The options document still answers, so the portal can say it does not book online.
    options = client.get("/api/patient/booking/options")
    assert options.status_code == 200, options.text
    assert options.json()["self_booking"] is False
    assert _diary(engine, _OWNER) == _diary(engine, _COLLEAGUE) == []
