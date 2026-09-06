# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""The watchdog (``app.claims.watchdog``): timeouts and the deadline ladder.

Each timeout row of the state table is a test; the reminder a stall
writes is checked against a real ``compliance_items`` table for carrying
the control number, the state and the age and nothing about the person;
and the deadline ladder is run repeatedly to prove each rung fires once.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import TYPE_CHECKING

import pytest
from app.claims.events import compliance_reminder_listener, register_claim_event_listener
from app.claims.watchdog import run_watchdog
from app.db import _reset_search_path_on_checkin
from app.db.models import ComplianceItemRow
from sqlalchemy import Engine, create_engine, event, select
from sqlalchemy.orm import Session

from tests.claims_fixtures import SERVICE_DATE, USER_ID, line
from tests.claims_pipeline_fakes import NOW, PipelineHarness, make_harness, restore_listeners

if TYPE_CHECKING:
    from collections.abc import Iterator


@pytest.fixture
def harness() -> Iterator[PipelineHarness]:
    built = make_harness()
    yield built
    restore_listeners()


def _run(harness: PipelineHarness) -> object:
    return run_watchdog(
        harness.pipeline, payers=harness.payers, practice_user_ids=harness.practice_users()
    )


def _at(days: int, *, now: datetime = NOW) -> datetime:
    return now - timedelta(days=days)


def _far_future_service(harness: PipelineHarness, **overrides: object) -> object:
    """A claim whose filing deadline is nowhere near, so only the timeout fires."""
    recent = line(id="dddddddd-dddd-4ddd-8ddd-dddddddddddd", service_date=NOW.date())
    return harness.add(lines=[recent], **overrides)


# --- the timeout rows --------------------------------------------------------------


def test_a_submission_nobody_confirmed_in_three_days_stalls(harness: PipelineHarness) -> None:
    created = _far_future_service(
        harness,
        state="validated",
        submission_pending_at=_at(4),
        submission_idempotency_key="k",
    )

    _run(harness)

    saved = harness.get(created.id)  # type: ignore[attr-defined]
    assert saved.state == "stalled"
    assert saved.submission_pending_at is None
    [event] = harness.listener.events
    assert event.kind == "stalled"
    assert event.detail.codes[0].code == "submission_unconfirmed"


@pytest.mark.parametrize("state", ["submitted", "ch_accepted"])
def test_six_days_without_a_payer_acknowledgment_stalls(
    harness: PipelineHarness, state: str
) -> None:
    created = _far_future_service(harness, state=state, submitted_at=_at(6))

    summary = _run(harness)

    saved = harness.get(created.id)  # type: ignore[attr-defined]
    assert summary.stalled == 1  # type: ignore[attr-defined]
    assert saved.state == "stalled"
    [receipt] = harness.receipts.list_for_claim(saved.id)
    assert (receipt.kind, receipt.from_state, receipt.to_state) == ("stalled", state, "stalled")
    [event] = harness.listener.events
    assert event.detail.codes[0].code == "no_payer_acknowledgment"
    assert "6 days" in (event.detail.codes[0].description or "")


def test_four_days_without_a_payer_acknowledgment_is_still_waiting(
    harness: PipelineHarness,
) -> None:
    created = _far_future_service(harness, state="submitted", submitted_at=_at(4))

    _run(harness)

    assert harness.get(created.id).state == "submitted"  # type: ignore[attr-defined]
    assert harness.listener.events == []


def test_thirty_one_days_without_a_remittance_stalls_and_names_enrollment(
    harness: PipelineHarness,
) -> None:
    created = _far_future_service(
        harness, state="payer_accepted", submitted_at=_at(40), payer_accepted_at=_at(31)
    )

    _run(harness)

    assert harness.get(created.id).state == "stalled"  # type: ignore[attr-defined]
    [event] = harness.listener.events
    code = event.detail.codes[0]
    assert code.code == "no_remittance"
    assert "confirm remittance enrollment is live" in (code.description or "")


@pytest.mark.parametrize("state", ["stalled", "paid", "rejected", "draft"])
def test_states_with_no_clock_are_left_alone(harness: PipelineHarness, state: str) -> None:
    created = _far_future_service(
        harness, state=state, submitted_at=_at(40), payer_accepted_at=_at(40)
    )

    _run(harness)

    assert harness.get(created.id).state == state  # type: ignore[attr-defined]
    assert harness.listener.kinds() == []


# --- the reminder a stall writes, on a real table ----------------------------------


@pytest.fixture
def reminders_engine() -> Iterator[Engine]:
    event.remove(Engine, "checkin", _reset_search_path_on_checkin)
    engine = create_engine("sqlite://")
    ComplianceItemRow.__table__.create(engine)  # type: ignore[attr-defined]  # SQLAlchemy declarative attr
    try:
        yield engine
    finally:
        engine.dispose()
        event.listen(Engine, "checkin", _reset_search_path_on_checkin)


def test_the_stalled_reminder_carries_no_patient_identifiers(
    harness: PipelineHarness, reminders_engine: Engine
) -> None:
    register_claim_event_listener(compliance_reminder_listener)
    created = _far_future_service(
        harness, state="submitted", submitted_at=_at(6), control_number="STALLED00001"
    )
    with Session(reminders_engine) as session:
        harness.pipeline.session = session
        _run(harness)
        session.commit()
        rows = list(session.execute(select(ComplianceItemRow)).scalars().all())

    [row] = rows
    assert row.user_id == USER_ID
    assert row.item_type == "claim_stalled"
    assert created.control_number[:8] in row.label  # type: ignore[attr-defined]
    assert "stalled" in row.label
    assert "6 days" in (row.notes or "")
    text = f"{row.label}\n{row.notes}"
    for forbidden in ("John", "Anon", "123456789", "2000-01-01", "F41", "Random St", "3335555"):
        assert forbidden not in text


# --- the deadline ladder ----------------------------------------------------------------


def _claim_with_days_left(harness: PipelineHarness, days_left: int, **overrides: object) -> object:
    """A draft whose filing deadline (90 days from service) is ``days_left`` away."""
    service = NOW.date() - timedelta(days=90 - days_left)
    service_line = line(id="eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee", service_date=service)
    overrides.setdefault("state", "draft")
    return harness.add(lines=[service_line], **overrides)


def test_each_rung_fires_once_across_repeated_runs(harness: PipelineHarness) -> None:
    created = _claim_with_days_left(harness, 10)

    _run(harness)
    _run(harness)

    [receipt] = harness.receipts.list_for_claim(created.id)  # type: ignore[attr-defined]
    assert (receipt.kind, receipt.deadline_kind, receipt.rung) == (
        "deadline_approaching",
        "filing",
        14,
    )
    [event] = harness.listener.events
    assert event.kind == "deadline_approaching"
    assert event.detail.deadline_kind == "filing"
    assert event.detail.days_left == 10
    assert event.detail.deadline_date == NOW.date() + timedelta(days=10)


def test_the_ladder_climbs_as_the_deadline_nears(harness: PipelineHarness) -> None:
    created = _claim_with_days_left(harness, 10)
    _run(harness)
    harness.pipeline.now = lambda: NOW + timedelta(days=5)
    _run(harness)
    _run(harness)
    harness.pipeline.now = lambda: NOW + timedelta(days=9)
    _run(harness)

    rungs = [r.rung for r in harness.receipts.list_for_claim(created.id)]  # type: ignore[attr-defined]
    assert rungs == [14, 7, 2]
    assert harness.listener.kinds() == ["deadline_approaching"] * 3


def test_a_missed_deadline_is_announced_once(harness: PipelineHarness) -> None:
    created = _claim_with_days_left(harness, 1)
    harness.pipeline.now = lambda: NOW + timedelta(days=1)
    _run(harness)
    harness.pipeline.now = lambda: NOW + timedelta(days=30)
    _run(harness)

    receipts = harness.receipts.list_for_claim(created.id)  # type: ignore[attr-defined]
    assert [(r.kind, r.rung) for r in receipts] == [("deadline_missed", 0)]
    [event] = harness.listener.events
    assert event.kind == "deadline_missed"
    assert event.detail.days_left == 0


def test_a_claim_with_weeks_to_go_raises_nothing(harness: PipelineHarness) -> None:
    _claim_with_days_left(harness, 40)

    _run(harness)

    assert harness.listener.events == []


def test_a_denied_claim_is_under_the_correction_clock(harness: PipelineHarness) -> None:
    created = harness.add(
        state="denied",
        submitted_at=_at(70),
        payer_accepted_at=_at(65),
        adjudicated_at=_at(55),
        lines=[line(service_date=SERVICE_DATE)],
    )

    _run(harness)

    [receipt] = harness.receipts.list_for_claim(created.id)
    assert (receipt.kind, receipt.deadline_kind, receipt.rung) == (
        "deadline_approaching",
        "correction",
        7,
    )
    [event] = harness.listener.events
    assert event.detail.deadline_kind == "correction"
    assert event.detail.days_left == 5


def test_a_stall_and_a_deadline_on_the_same_claim_are_both_raised(
    harness: PipelineHarness,
) -> None:
    created = _claim_with_days_left(harness, 3, state="submitted", submitted_at=_at(6))

    _run(harness)

    saved = harness.get(created.id)  # type: ignore[attr-defined]
    assert saved.state == "stalled"
    assert harness.listener.kinds() == ["stalled", "deadline_approaching"]


def test_today_is_the_pipelines_clock_not_the_wall_clock() -> None:
    fixed = datetime(2030, 1, 1, tzinfo=UTC)
    harness = make_harness(now=fixed)
    try:
        created = harness.add(
            state="draft",
            lines=[line(service_date=date(2029, 10, 10))],
        )
        _run(harness)
        [receipt] = harness.receipts.list_for_claim(created.id)
        assert receipt.detail == {"deadline_date": "2030-01-08", "days_left": 7}
    finally:
        restore_listeners()
