# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Tests for the claim lifecycle event seam (``app.claims.events``).

The listener tests run against a real SQLAlchemy session over in-memory
SQLite with only the ``compliance_items`` table created, so "the caller's
transaction still commits" and "one row per event" are checked against a
database rather than a fake.
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import UTC, date, datetime
from typing import TYPE_CHECKING, get_args

import pytest
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
    register_claim_event_listener,
)
from app.compliance import get_template, list_templates_for_edition
from app.db import _reset_search_path_on_checkin
from app.db.models import ComplianceItemRow
from app.main import app
from app.repositories import get_compliance_item_repository
from app.repositories.postgres.compliance_item import PostgresComplianceItemRepository
from sqlalchemy import Engine, create_engine, event, select
from sqlalchemy.orm import Session

if TYPE_CHECKING:
    from collections.abc import Iterator

    from fastapi.testclient import TestClient

_USER_ID = "11111111-1111-4111-8111-111111111111"
_CLAIM_ID = "22222222-2222-4222-8222-222222222222"
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


@pytest.fixture
def engine() -> Iterator[Engine]:
    """In-memory SQLite with only ``compliance_items`` created.

    The pool-checkin hook in ``app.db`` issues a Postgres ``SET search_path``
    that SQLite cannot parse, so it is detached for the life of this engine
    and put back afterwards.
    """
    event.remove(Engine, "checkin", _reset_search_path_on_checkin)
    eng = create_engine("sqlite://")
    ComplianceItemRow.__table__.create(eng)  # type: ignore[attr-defined]  # SQLAlchemy declarative attr
    try:
        yield eng
    finally:
        eng.dispose()
        event.listen(Engine, "checkin", _reset_search_path_on_checkin)


@pytest.fixture
def listeners() -> Iterator[None]:
    """Start each test with no listeners; put the default back afterwards."""
    clear_claim_event_listeners()
    yield
    clear_claim_event_listeners()
    register_claim_event_listener(compliance_reminder_listener)


def _reminders(engine: Engine) -> list[ComplianceItemRow]:
    with Session(engine) as session:
        return list(session.execute(select(ComplianceItemRow)).scalars().all())


# --- registry ------------------------------------------------------------------


@pytest.mark.usefixtures("listeners")
def test_listeners_run_in_registration_order(engine: Engine) -> None:
    calls: list[str] = []
    register_claim_event_listener(lambda _s, _e: calls.append("first"))
    register_claim_event_listener(lambda _s, _e: calls.append("second"))
    register_claim_event_listener(lambda _s, _e: calls.append("third"))

    with Session(engine) as session:
        emit(session, _event())

    assert calls == ["first", "second", "third"]


@pytest.mark.usefixtures("listeners")
def test_emit_with_no_listeners_is_a_no_op(engine: Engine) -> None:
    with Session(engine) as session:
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

    with caplog.at_level(logging.WARNING, logger="app.claims.events"), Session(engine) as session:
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

    with Session(engine) as session:
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

    with Session(engine) as session:
        emit(session, _event("denied"))
        emit(session, _event("denied"))
        session.commit()
    with Session(engine) as session:
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

    with Session(engine) as session:
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

    with Session(engine) as session:
        emit(session, _event("paid"))
        session.commit()

    assert _reminders(engine) == []


@pytest.mark.usefixtures("listeners")
def test_reminder_without_a_deadline_is_due_a_week_after_the_event(engine: Engine) -> None:
    register_claim_event_listener(compliance_reminder_listener)

    with Session(engine) as session:
        emit(session, _event("stalled", payer_name=None))
        session.commit()

    [row] = _reminders(engine)
    assert row.due_date == date(2026, 9, 13)
    assert row.label == "Claim PCN20260 stalled at payer"
    assert row.notes == f"Claim control number: {_CONTROL_NUMBER}"


@pytest.mark.usefixtures("listeners")
def test_enrollment_reminder_carries_the_payers_instructions(engine: Engine) -> None:
    register_claim_event_listener(compliance_reminder_listener)

    with Session(engine) as session:
        emit(session, _event("enrollment_action_required"))
        session.commit()

    [row] = _reminders(engine)
    assert row.item_type == "claim_enrollment_action_required"
    assert row.label == "Claim PCN20260 enrollment action needed for Aetna"
    assert row.notes is not None
    assert row.notes.endswith("Sign and return the EFT authorization form.")


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
def test_compliance_route_accepts_claim_item_types(client: TestClient, item_type: str) -> None:
    created: list[object] = []

    class _Repo:
        def create(self, item: object) -> object:
            created.append(item)
            return item

    app.dependency_overrides[get_compliance_item_repository] = _Repo
    try:
        response = client.post(
            "/api/compliance",
            json={"item_type": item_type, "label": "Claim PCN20260 denied by Aetna"},
        )
    finally:
        app.dependency_overrides.pop(get_compliance_item_repository, None)

    assert response.status_code == 201, response.text
    assert response.json()["item_type"] == item_type
    assert len(created) == 1


def test_postgres_repository_reads_what_the_listener_wrote(engine: Engine) -> None:
    """The dashboard reads reminders through the repository; the listener's rows fit it."""
    with Session(engine) as session:
        compliance_reminder_listener(session, _event("deadline_missed"))
        session.commit()
        [item] = PostgresComplianceItemRepository(session).list_by_user(_USER_ID)

    assert item.item_type == "claim_deadline_missed"
    assert item.label == "Claim PCN20260 filing deadline missed with Aetna, by 2026-09-01"
