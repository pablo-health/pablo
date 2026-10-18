# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""A refill request's due time, against real PostgreSQL.

What a unit test cannot prove:

* the due time is read from the practice's own setting and its owner's
  availability rules, through a session armed as the owner, connected as the
  ``NOBYPASSRLS`` role production has, so a rule hidden by row security
  would silently drop the clinician's working week back to Monday to Friday;
* a practice whose owner has set no hours counts Monday to Friday;
* the queue orders soonest due first and puts requests with no due time
  last, which is Postgres's ``NULLS LAST`` and not its default for ``ASC``.

The working-day arithmetic itself is covered in ``tests/test_working_days.py``.

Run: ``make test-integration``.
"""

from __future__ import annotations

import os
import uuid
from datetime import UTC, date, datetime, timedelta
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session as OrmSession

if TYPE_CHECKING:
    from collections.abc import Iterator

    from sqlalchemy.engine import Engine

_DB_URL = os.environ.get("DATABASE_URL", "")
pytestmark = pytest.mark.skipif(
    not _DB_URL or os.environ.get("DATABASE_BACKEND") != "postgres",
    reason=(
        "PostgreSQL not configured. Set DATABASE_URL and DATABASE_BACKEND=postgres "
        "or run via make test-integration."
    ),
)

#: A Monday, 11:00 in New York — the zone an owner with no saved preference has.
_SUBMITTED = datetime(2026, 10, 12, 15, 0, tzinfo=UTC)
_NEW_YORK = ZoneInfo("America/New_York")


def _provision(engine: Engine) -> str:
    from app.db.provisioning import create_practice_schema  # noqa: PLC0415

    with engine.connect() as conn:
        conn.execute(text("SET search_path = practice, platform, public"))
        conn.commit()
    schema = f"practice_test_turnaround_{uuid.uuid4().hex[:8]}"
    create_practice_schema(engine, schema)
    return schema


def _register_practice(engine: Engine, schema: str, owner: str, *, days: int | None) -> None:
    from app.db.platform_models import PracticePortalSettingsRow, PracticeRow  # noqa: PLC0415

    practice_id = f"practice-turnaround-{uuid.uuid4().hex[:8]}"
    now = datetime.now(UTC)
    with OrmSession(bind=engine) as session:
        session.add(
            PracticeRow(
                id=practice_id,
                name="Example Therapy",
                schema_name=schema,
                owner_email=f"{practice_id}@example.test",
                owner_user_id=owner,
                created_at=now,
            )
        )
        session.flush()
        if days is not None:
            session.add(
                PracticePortalSettingsRow(
                    practice_id=practice_id,
                    enabled=True,
                    refill_turnaround_days=days,
                    updated_at=now,
                )
            )
        session.commit()


def _working_hours(engine: Engine, schema: str, owner: str, weekdays: tuple[int, ...]) -> None:
    with engine.begin() as conn:
        conn.execute(text(f"SET search_path = {schema}, platform, public"))
        conn.execute(text("SELECT set_config('app.current_user_id', :u, false)"), {"u": owner})
        for weekday in weekdays:
            conn.execute(
                text(
                    "INSERT INTO availability_rules (id, user_id, rule_type, enforcement, "
                    "params, created_at) VALUES (gen_random_uuid(), CAST(:u AS uuid), "
                    "'working_hours', 'hard', CAST(:params AS jsonb), now())"
                ),
                {
                    "u": owner,
                    "params": f'{{"day_of_week": {weekday}, "start": "09:00", "end": "17:00"}}',
                },
            )


@pytest.fixture(scope="module")
def schemas(engine: Engine) -> Iterator[list[str]]:
    created: list[str] = []
    yield created
    with engine.connect() as conn:
        for schema in created:
            conn.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        conn.commit()


class TestDueTimeFromThePractice:
    def test_counts_the_owners_working_days_and_the_practices_target(
        self, engine: Engine, schemas: list[str]
    ) -> None:
        from app.services.refill_turnaround import practice_refill_due_at  # noqa: PLC0415

        owner = str(uuid.uuid4())
        schema = _provision(engine)
        schemas.append(schema)
        _register_practice(engine, schema, owner, days=3)
        _working_hours(engine, schema, owner, (0, 3))  # Mondays and Thursdays

        due = practice_refill_due_at(schema, _SUBMITTED)

        # Thursday the 15th, Monday the 19th, Thursday the 22nd.
        local = due.astimezone(_NEW_YORK)
        assert local.date() == date(2026, 10, 22)
        assert (local.hour, local.minute) == (23, 59)

    def test_an_owner_with_no_hours_counts_monday_to_friday(
        self, engine: Engine, schemas: list[str]
    ) -> None:
        from app.services.refill_turnaround import practice_refill_due_at  # noqa: PLC0415

        owner = str(uuid.uuid4())
        schema = _provision(engine)
        schemas.append(schema)
        _register_practice(engine, schema, owner, days=None)  # default target of 2

        due = practice_refill_due_at(schema, _SUBMITTED)

        assert due.astimezone(_NEW_YORK).date() == date(2026, 10, 14)  # Wednesday


class TestQueueOrder:
    def test_soonest_due_first_and_no_due_time_last(
        self, engine: Engine, schemas: list[str]
    ) -> None:
        from app.db.tenant_session import tenant_db_session  # noqa: PLC0415
        from app.repositories.postgres.refill_request import (  # noqa: PLC0415
            PostgresRefillRequestRepository,
        )

        clinician = str(uuid.uuid4())
        schema = _provision(engine)
        schemas.append(schema)
        patient = str(uuid.uuid4())
        now = datetime.now(UTC)
        with engine.begin() as conn:
            conn.execute(text(f"SET search_path = {schema}, platform, public"))
            conn.execute(
                text("SELECT set_config('app.current_user_id', :u, false)"), {"u": clinician}
            )
            conn.execute(
                text(
                    "INSERT INTO patients (id, first_name, last_name, first_name_lower, "
                    "last_name_lower, status, session_count, created_at, updated_at) "
                    "VALUES (CAST(:pid AS uuid), 'Ada', 'Lovelace', 'ada', 'lovelace', "
                    "'active', 0, now(), now())"
                ),
                {"pid": patient},
            )
            conn.execute(
                text(
                    "INSERT INTO patient_clinicians (patient_id, user_id, granted_by) "
                    "VALUES (CAST(:pid AS uuid), :u, :u)"
                ),
                {"pid": patient, "u": clinician},
            )
            ids: dict[str, str] = {}
            # The oldest has no due time; created order is the reverse of due order.
            for name, created_minutes_ago, due_in_days in (
                ("undated", 90, None),
                ("later", 60, 3),
                ("sooner", 30, 1),
            ):
                ids[name] = str(uuid.uuid4())
                created = now - timedelta(minutes=created_minutes_ago)
                conn.execute(
                    text(
                        "INSERT INTO refill_requests (id, patient_id, medication_text, status, "
                        "due_at, created_at, updated_at) VALUES (CAST(:id AS uuid), "
                        "CAST(:pid AS uuid), 'Sertraline 50 mg', 'requested', :due, :at, :at)"
                    ),
                    {
                        "id": ids[name],
                        "pid": patient,
                        "due": None if due_in_days is None else now + timedelta(days=due_in_days),
                        "at": created,
                    },
                )

        with tenant_db_session(schema, clinician) as session:
            queue = PostgresRefillRequestRepository(session).list_queue(clinician, "pending")

        assert [e.request.id for e in queue] == [ids["sooner"], ids["later"], ids["undated"]]
        assert queue[0].request.due_at is not None
