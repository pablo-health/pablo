# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Tests for the claim lifecycle event seam (``app.claims.events``).

The listener tests run against a real provisioned practice schema, so "the
caller's transaction still commits" and "one row per event" are checked
against the database that will actually run them — reminders land on a
row-secured table, and a listener that wrote rows nobody could read back
would look identical to one that worked.
"""

from __future__ import annotations

import json
import logging
import os
import uuid
from contextlib import contextmanager
from datetime import UTC, date, datetime
from pathlib import Path
from typing import TYPE_CHECKING, get_args

import pytest
from alembic import command
from alembic.config import Config
from app.auth.route_access import subscription_exempt
from app.auth.service import get_current_user, get_tenant_context
from app.claims import events
from app.claims.events import (
    COMPLIANCE_ITEM_TYPES,
    ClaimEvent,
    ClaimEventDetail,
    ClaimEventKind,
    CodeRef,
    clear_claim_event_listeners,
    compliance_item_type,
    compliance_reminder_listener,
    emit,
    find_claim_reminder,
    register_claim_event_listener,
    resolve_compliance_reminder,
)
from app.compliance import get_template, list_templates_for_edition
from app.db import arm_current_user_id, set_tenant_schema
from app.db.models import ComplianceItemRow
from app.db.provisioning import create_practice_schema
from app.models import User
from app.repositories import get_compliance_item_repository
from app.repositories.postgres.compliance_item import PostgresComplianceItemRepository
from app.routes import compliance as compliance_routes
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import Engine, create_engine, select, text
from sqlalchemy.orm import Session

if TYPE_CHECKING:
    from collections.abc import Iterator

_DB_URL = os.environ.get("DATABASE_URL", "")
pytestmark = pytest.mark.skipif(
    not _DB_URL or os.environ.get("DATABASE_BACKEND") != "postgres",
    reason=(
        "PostgreSQL not configured. Set DATABASE_URL and "
        "DATABASE_BACKEND=postgres; testcontainers should set both."
    ),
)

_SUFFIX = uuid.uuid4().hex[:8]
_SCHEMA = f"practice_test_claimev_{_SUFFIX}"

_USER_ID = str(uuid.uuid4())
_OTHER_USER_ID = str(uuid.uuid4())
_CLAIM_ID = str(uuid.uuid4())
_CONTROL_NUMBER = "PCN20260906ABC"
_OCCURRED_AT = datetime(2026, 9, 6, 15, 30, tzinfo=UTC)

_ALL_KINDS: tuple[ClaimEventKind, ...] = get_args(ClaimEventKind)


_DETAILS: dict[ClaimEventKind, ClaimEventDetail] = {
    "rejected": ClaimEventDetail(
        codes=(
            CodeRef("edit", "A7:562", "Invalid rendering provider NPI"),
            CodeRef("status", "A7", "Rejected for invalid information"),
        ),
        deadline_kind="correction",
        deadline_date=date(2026, 9, 20),
        days_left=14,
    ),
    "denied": ClaimEventDetail(
        codes=(
            CodeRef("carc", "197", "Precertification/authorization absent"),
            CodeRef("rarc", "N54", "Claim information is inconsistent"),
        ),
        deadline_kind="appeal",
        deadline_date=date(2026, 12, 5),
        days_left=90,
    ),
    "partial": ClaimEventDetail(
        codes=(CodeRef("carc", "45", "Charge exceeds fee schedule"),),
        amount_cents=8_500,
    ),
    "paid": ClaimEventDetail(amount_cents=12_000),
    "enrollment_action_required": ClaimEventDetail(
        payer_instructions="Sign and return the EFT authorization form.",
    ),
    "deadline_approaching": ClaimEventDetail(
        deadline_kind="filing", deadline_date=date(2026, 9, 10), days_left=4
    ),
    "deadline_missed": ClaimEventDetail(
        deadline_kind="filing", deadline_date=date(2026, 9, 1), days_left=-5
    ),
}
"""A realistic detail per kind: codes, a deadline, an amount, instructions."""


def _detail(kind: ClaimEventKind) -> ClaimEventDetail:
    return _DETAILS.get(kind, ClaimEventDetail())


def _event(kind: ClaimEventKind = "rejected", **overrides: object) -> ClaimEvent:
    fields: dict[str, object] = {
        "kind": kind,
        "control_number": _CONTROL_NUMBER,
        "claim_id": _CLAIM_ID,
        "user_id": _USER_ID,
        "payer_id": "60054",
        "payer_name": "Aetna",
        "state": kind,
        "occurred_at": _OCCURRED_AT,
        "detail": _detail(kind),
    }
    fields.update(overrides)
    return ClaimEvent(**fields)  # type: ignore[arg-type]  # overrides are test-typed


@pytest.fixture(scope="module")
def _database() -> Iterator[Engine]:
    """The real database, migrated, with a practice schema of this module's own."""
    backend_dir = Path(__file__).resolve().parents[2]
    cfg = Config(str(backend_dir / "alembic.ini"))
    cfg.set_main_option("script_location", str(backend_dir / "alembic"))
    command.upgrade(cfg, "head")
    eng = create_engine(_DB_URL, pool_pre_ping=True)
    create_practice_schema(eng, _SCHEMA)
    yield eng
    with eng.connect() as conn:
        conn.execute(text(f'DROP SCHEMA IF EXISTS "{_SCHEMA}" CASCADE'))
        conn.commit()
    eng.dispose()


@pytest.fixture
def engine(_database: Engine) -> Iterator[Engine]:
    """The same engine, emptied of reminders between tests."""
    yield _database
    with _database.connect() as conn:
        conn.execute(text(f"TRUNCATE {_SCHEMA}.compliance_items CASCADE"))
        conn.commit()


@contextmanager
def _session(engine: Engine) -> Iterator[Session]:
    """A session scoped to the schema and armed as the clinician.

    Every session in this module goes through here. A reminder is written
    under a row policy keyed on the clinician, so an unarmed session would
    write nothing readable and fail in a way that looks like a listener bug.
    """
    with Session(engine) as session:
        set_tenant_schema(session, _SCHEMA)
        arm_current_user_id(session, _USER_ID)
        yield session


@pytest.fixture
def listeners() -> Iterator[None]:
    """Start each test with no listeners; put the default back afterwards."""
    clear_claim_event_listeners()
    yield
    clear_claim_event_listeners()
    register_claim_event_listener(compliance_reminder_listener)


def _reminders(engine: Engine) -> list[ComplianceItemRow]:
    with _session(engine) as session:
        return list(session.execute(select(ComplianceItemRow)).scalars().all())


# --- registry ------------------------------------------------------------------


@pytest.mark.usefixtures("listeners")
def test_listeners_run_in_registration_order(engine: Engine) -> None:
    calls: list[str] = []
    register_claim_event_listener(lambda _s, _e: calls.append("first"))
    register_claim_event_listener(lambda _s, _e: calls.append("second"))
    register_claim_event_listener(lambda _s, _e: calls.append("third"))

    with _session(engine) as session:
        emit(session, _event())

    assert calls == ["first", "second", "third"]


@pytest.mark.usefixtures("listeners")
def test_emit_with_no_listeners_is_a_no_op(engine: Engine) -> None:
    with _session(engine) as session:
        emit(session, _event())


def test_default_listener_is_registered_at_import() -> None:
    assert compliance_reminder_listener in events._listeners


@pytest.mark.usefixtures("listeners")
def test_raising_listener_is_logged_and_the_rest_still_run(
    engine: Engine, caplog: pytest.LogCaptureFixture
) -> None:
    calls: list[str] = []

    def explodes(_session: Session, _event: ClaimEvent) -> None:
        raise RuntimeError("member 123456789 dob 1980-01-01 F41.1 subscriber Jane")

    register_claim_event_listener(explodes)
    register_claim_event_listener(lambda _s, _e: calls.append("after"))

    with caplog.at_level(logging.WARNING, logger="app.claims.events"), _session(engine) as session:
        emit(session, _event("denied"))

    assert calls == ["after"]
    [record] = [r for r in caplog.records if r.name == "app.claims.events"]
    assert record.levelno == logging.WARNING
    message = record.getMessage()
    assert "explodes" in message
    assert "denied" in message
    assert _CONTROL_NUMBER in message
    assert "RuntimeError" in message
    for forbidden in ("member", "dob", "F41", "subscriber", "Precertification"):
        assert forbidden not in message


@pytest.mark.usefixtures("listeners")
def test_raising_listener_does_not_stop_the_callers_commit(engine: Engine) -> None:
    def explodes(_session: Session, _event: ClaimEvent) -> None:
        raise RuntimeError("boom")

    register_claim_event_listener(explodes)
    register_claim_event_listener(compliance_reminder_listener)

    with _session(engine) as session:
        # The caller's own state change, recorded before the event fires.
        session.add(
            ComplianceItemRow(
                id=str(uuid.uuid4()),
                user_id=_USER_ID,
                item_type="license",
                label="Professional license",
                due_date=None,
                notes=None,
                completed_at=None,
                created_at=_OCCURRED_AT,
                updated_at=_OCCURRED_AT,
            )
        )
        emit(session, _event("rejected"))
        session.commit()

    rows = _reminders(engine)
    assert sorted(r.item_type for r in rows) == ["claim_rejected", "license"]


# --- event shape ---------------------------------------------------------------


@pytest.mark.parametrize("kind", _ALL_KINDS)
def test_serialised_event_carries_no_clinical_identifiers(kind: ClaimEventKind) -> None:
    serialised = json.dumps(_event(kind).to_dict()).lower()
    for forbidden in ("member", "dob", "f41", "subscriber"):
        assert forbidden not in serialised


def test_to_dict_round_trips_the_fields_a_listener_needs() -> None:
    payload = _event("denied").to_dict()
    assert payload["kind"] == "denied"
    assert payload["control_number"] == _CONTROL_NUMBER
    assert payload["claim_id"] == _CLAIM_ID
    assert payload["user_id"] == _USER_ID
    assert payload["payer_name"] == "Aetna"
    assert payload["occurred_at"] == "2026-09-06T15:30:00+00:00"
    detail = payload["detail"]
    assert isinstance(detail, dict)
    assert detail["deadline_kind"] == "appeal"
    assert detail["deadline_date"] == "2026-12-05"
    assert detail["days_left"] == 90
    assert detail["codes"] == [
        {"system": "carc", "code": "197", "description": "Precertification/authorization absent"},
        {"system": "rarc", "code": "N54", "description": "Claim information is inconsistent"},
    ]


def test_events_are_immutable() -> None:
    event = _event()
    with pytest.raises(AttributeError):
        event.kind = "paid"  # type: ignore[misc]  # the point of the test


# --- default listener ----------------------------------------------------------


@pytest.mark.usefixtures("listeners")
def test_default_listener_writes_one_reminder_per_kind_and_control_number(
    engine: Engine,
) -> None:
    register_claim_event_listener(compliance_reminder_listener)

    with _session(engine) as session:
        emit(session, _event("denied"))
        emit(session, _event("denied"))
        session.commit()
    with _session(engine) as session:
        emit(session, _event("denied"))
        session.commit()

    [row] = _reminders(engine)
    assert row.user_id == _USER_ID
    assert row.item_type == "claim_denied"
    assert row.label == "Claim PCN20260 denied by Aetna, by 2026-12-05"
    assert row.due_date == date(2026, 12, 5)
    assert row.notes is not None
    assert row.notes.splitlines() == [
        f"Claim control number: {_CONTROL_NUMBER}",
        "Precertification/authorization absent; Claim information is inconsistent",
    ]
    assert row.completed_at is None


@pytest.mark.usefixtures("listeners")
def test_same_control_number_different_kinds_get_separate_reminders(engine: Engine) -> None:
    register_claim_event_listener(compliance_reminder_listener)

    with _session(engine) as session:
        emit(session, _event("rejected"))
        emit(session, _event("denied"))
        emit(session, _event("rejected", control_number="OTHER-CLAIM"))
        session.commit()

    rows = _reminders(engine)
    assert sorted((r.item_type, r.notes.splitlines()[0] if r.notes else "") for r in rows) == [
        ("claim_denied", f"Claim control number: {_CONTROL_NUMBER}"),
        ("claim_rejected", "Claim control number: OTHER-CLAIM"),
        ("claim_rejected", f"Claim control number: {_CONTROL_NUMBER}"),
    ]


@pytest.mark.usefixtures("listeners")
def test_paid_writes_no_reminder(engine: Engine) -> None:
    register_claim_event_listener(compliance_reminder_listener)

    with _session(engine) as session:
        emit(session, _event("paid"))
        session.commit()

    assert _reminders(engine) == []


@pytest.mark.usefixtures("listeners")
def test_reminder_without_a_deadline_is_due_a_week_after_the_event(engine: Engine) -> None:
    register_claim_event_listener(compliance_reminder_listener)

    with _session(engine) as session:
        emit(session, _event("stalled", payer_name=None))
        session.commit()

    [row] = _reminders(engine)
    assert row.due_date == date(2026, 9, 13)
    assert row.label == "Claim PCN20260 stalled at payer"
    assert row.notes == f"Claim control number: {_CONTROL_NUMBER}"


@pytest.mark.usefixtures("listeners")
def test_enrollment_reminder_carries_the_payers_instructions(engine: Engine) -> None:
    register_claim_event_listener(compliance_reminder_listener)

    with _session(engine) as session:
        emit(session, _event("enrollment_action_required"))
        session.commit()

    [row] = _reminders(engine)
    assert row.item_type == "claim_enrollment_action_required"
    assert row.label == "Claim PCN20260 enrollment action needed for Aetna"
    assert row.notes is not None
    assert row.notes.endswith("Sign and return the EFT authorization form.")


# --- finding a reminder --------------------------------------------------------


@pytest.mark.usefixtures("listeners")
def test_find_claim_reminder_returns_the_row_the_listener_wrote(engine: Engine) -> None:
    register_claim_event_listener(compliance_reminder_listener)

    with _session(engine) as session:
        emit(session, _event("denied"))
        session.commit()

    with _session(engine) as session:
        row = find_claim_reminder(session, kind="denied", control_number=_CONTROL_NUMBER)

    assert row is not None
    assert row.item_type == "claim_denied"
    assert row.user_id == _USER_ID


@pytest.mark.usefixtures("listeners")
def test_find_claim_reminder_misses_another_claim_or_kind(engine: Engine) -> None:
    register_claim_event_listener(compliance_reminder_listener)

    with _session(engine) as session:
        emit(session, _event("denied"))
        session.commit()

    with _session(engine) as session:
        assert find_claim_reminder(session, kind="denied", control_number="OTHER-CLAIM") is None
        assert find_claim_reminder(session, kind="rejected", control_number=_CONTROL_NUMBER) is None
        assert (
            find_claim_reminder(
                session, kind="denied", control_number=_CONTROL_NUMBER, user_id=_OTHER_USER_ID
            )
            is None
        )


@pytest.mark.usefixtures("listeners")
def test_resolving_completes_the_reminder_the_lookup_returns(engine: Engine) -> None:
    register_claim_event_listener(compliance_reminder_listener)

    with _session(engine) as session:
        emit(session, _event("enrollment_action_required"))
        session.commit()

    with _session(engine) as session:
        assert resolve_compliance_reminder(
            session,
            kind="enrollment_action_required",
            control_number=_CONTROL_NUMBER,
            user_id=_USER_ID,
        )
        session.commit()

    with _session(engine) as session:
        row = find_claim_reminder(
            session, kind="enrollment_action_required", control_number=_CONTROL_NUMBER
        )
    assert row is not None
    assert row.completed_at is not None


# --- compliance template catalog -----------------------------------------------


def test_every_actionable_kind_has_a_compliance_template() -> None:
    expected = tuple(compliance_item_type(kind) for kind in _ALL_KINDS if kind != "paid")
    assert expected == COMPLIANCE_ITEM_TYPES
    visible = {t.item_type for t in list_templates_for_edition("core")}
    for item_type in COMPLIANCE_ITEM_TYPES:
        template = get_template(item_type)
        assert template is not None, item_type
        assert template.multi_instance, item_type
        assert item_type in visible


@pytest.mark.parametrize("item_type", COMPLIANCE_ITEM_TYPES)
def test_compliance_route_accepts_claim_item_types(item_type: str) -> None:
    """Every kind the listener can write is a kind the route will accept.

    A schema question, not a storage one — the repository is counted rather
    than queried — so this one runs on a router of its own with no database
    behind it.
    """
    created: list[object] = []

    class _Repo:
        def create(self, item: object) -> object:
            created.append(item)
            return item

    now = datetime.now(UTC)
    api = FastAPI()
    api.include_router(compliance_routes.router)
    api.dependency_overrides[get_current_user] = lambda: User(
        id=_USER_ID,
        email="therapist@example.com",
        name="Test Therapist",
        created_at=now,
        baa_accepted_at=now,
        baa_version="2024-01-01",
    )
    api.dependency_overrides[get_tenant_context] = lambda: None
    api.dependency_overrides[subscription_exempt] = lambda: None
    api.dependency_overrides[get_compliance_item_repository] = _Repo

    response = TestClient(api, raise_server_exceptions=False).post(
        "/api/compliance",
        json={"item_type": item_type, "label": "Claim PCN20260 denied by Aetna"},
    )

    assert response.status_code == 201, response.text
    assert response.json()["item_type"] == item_type
    assert len(created) == 1


def test_postgres_repository_reads_what_the_listener_wrote(engine: Engine) -> None:
    """The dashboard reads reminders through the repository; the listener's rows fit it."""
    with _session(engine) as session:
        compliance_reminder_listener(session, _event("deadline_missed"))
        session.commit()
        [item] = PostgresComplianceItemRepository(session).list_by_user(_USER_ID)

    assert item.item_type == "claim_deadline_missed"
    assert item.label == "Claim PCN20260 filing deadline missed with Aetna, by 2026-09-01"
