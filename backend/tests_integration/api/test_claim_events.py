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
from app.claims import events
from app.claims.events import (
    CLAIM_BACKED_KINDS,
    NON_CLAIM_KINDS,
    REMINDER_KINDS,
    ClaimEvent,
    ClaimEventDetail,
    ClaimEventKind,
    CodeRef,
    clear_claim_event_listeners,
    compliance_reminder_listener,
    emit,
    find_claim_reminder,
    register_claim_event_listener,
    resolve_compliance_reminder,
)
from app.db import arm_current_user_id, set_tenant_schema
from app.db.models import CLAIM_REMINDER_KINDS, ClaimReminderRow, ComplianceItemRow
from app.db.provisioning import create_practice_schema
from sqlalchemy import Engine, create_engine, select, text
from sqlalchemy.exc import IntegrityError
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
_OTHER_CLAIM_ID = str(uuid.uuid4())
_OTHER_CONTROL_NUMBER = "OTHER-CLAIM"
_PATIENT_ID = str(uuid.uuid4())
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
    _seed_claims(eng)
    yield eng
    with eng.connect() as conn:
        conn.execute(text(f'DROP SCHEMA IF EXISTS "{_SCHEMA}" CASCADE'))
        conn.commit()
    eng.dispose()


def _seed_claims(eng: Engine) -> None:
    """A client, a grant, a payer, a coverage and two claims.

    None of this was needed when a reminder was a compliance item: the row
    named its claim in a line of text, so the claim never had to exist. It was
    a plain uuid nobody had inserted, and every test in this module passed
    against it. That is what a foreign key buys — the reminder cannot point at
    a claim that was never filed.
    """
    with eng.begin() as conn:
        conn.execute(text(f"SET search_path = {_SCHEMA}, platform, public"))
        conn.execute(text("SELECT set_config('app.current_user_id', :u, false)"), {"u": _USER_ID})
        conn.execute(
            text(
                "INSERT INTO patients (id, first_name, last_name, first_name_lower, "
                "last_name_lower, status, session_count, created_at, updated_at) "
                "VALUES (CAST(:pid AS uuid), 'Test', 'Patient', 'test', 'patient', "
                "'active', 0, now(), now())"
            ),
            {"pid": _PATIENT_ID},
        )
        conn.execute(
            text(
                "INSERT INTO patient_clinicians (patient_id, user_id, granted_by) "
                "VALUES (CAST(:pid AS uuid), :u, :u)"
            ),
            {"pid": _PATIENT_ID, "u": _USER_ID},
        )
        payer_id = str(uuid.uuid4())
        conn.execute(
            text(
                "INSERT INTO payers (id, name, payer_id, created_at, updated_at) "
                "VALUES (CAST(:id AS uuid), 'Aetna', 'AETNA', now(), now())"
            ),
            {"id": payer_id},
        )
        coverage_id = str(uuid.uuid4())
        conn.execute(
            text(
                "INSERT INTO patient_coverage (id, patient_id, payer_id, member_id, "
                "subscriber_relationship, active, created_at, updated_at) "
                "VALUES (CAST(:id AS uuid), CAST(:pid AS uuid), CAST(:payer AS uuid), "
                "'123456789', 'self', true, now(), now())"
            ),
            {"id": coverage_id, "pid": _PATIENT_ID, "payer": payer_id},
        )
        for claim_id, control in (
            (_CLAIM_ID, _CONTROL_NUMBER),
            (_OTHER_CLAIM_ID, _OTHER_CONTROL_NUMBER),
        ):
            conn.execute(
                text(
                    "INSERT INTO claims (id, control_number, patient_id, coverage_id, "
                    "payer_id, state, frequency_code, total_charge_cents, "
                    "total_paid_cents, diagnosis_codes, billing_snapshot, "
                    "subscriber_snapshot, created_at, updated_at) "
                    "VALUES (CAST(:id AS uuid), :cn, CAST(:pid AS uuid), "
                    "CAST(:cov AS uuid), CAST(:payer AS uuid), 'submitted', '1', "
                    "10000, 0, '[]'::jsonb, '{}'::jsonb, '{}'::jsonb, now(), now())"
                ),
                {
                    "id": claim_id,
                    "cn": control,
                    "pid": _PATIENT_ID,
                    "cov": coverage_id,
                    "payer": payer_id,
                },
            )


@pytest.fixture
def engine(_database: Engine) -> Iterator[Engine]:
    """The same engine, emptied of reminders between tests."""
    yield _database
    with _database.connect() as conn:
        conn.execute(text(f"TRUNCATE {_SCHEMA}.claim_reminders CASCADE"))
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


def _reminders(engine: Engine) -> list[ClaimReminderRow]:
    with _session(engine) as session:
        return list(session.execute(select(ClaimReminderRow)).scalars().all())


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

    # The caller's own row survived the exploding listener, and the reminder
    # the surviving listener wrote is in its own table beside it.
    with _session(engine) as session:
        items = list(session.execute(select(ComplianceItemRow)).scalars().all())
    assert [i.item_type for i in items] == ["license"]
    assert [r.kind for r in _reminders(engine)] == ["rejected"]


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
    assert row.claim_id == _CLAIM_ID
    assert row.patient_id == _PATIENT_ID
    assert row.kind == "denied"
    assert row.label == "Claim PCN20260 denied by Aetna, by 2026-12-05"
    assert row.due_date == date(2026, 12, 5)
    # The control number is gone from the notes. It was only ever there
    # because the lookup needed somewhere to find it, and the claim_id above
    # is that somewhere now. What is left is what the payer said.
    assert row.notes == ("Precertification/authorization absent; Claim information is inconsistent")
    assert row.completed_at is None


@pytest.mark.usefixtures("listeners")
def test_same_control_number_different_kinds_get_separate_reminders(engine: Engine) -> None:
    register_claim_event_listener(compliance_reminder_listener)

    with _session(engine) as session:
        emit(session, _event("rejected"))
        emit(session, _event("denied"))
        emit(
            session,
            _event("rejected", control_number=_OTHER_CONTROL_NUMBER, claim_id=_OTHER_CLAIM_ID),
        )
        session.commit()

    rows = _reminders(engine)
    assert sorted((r.kind, r.claim_id) for r in rows) == sorted(
        [
            ("denied", _CLAIM_ID),
            ("rejected", _CLAIM_ID),
            ("rejected", _OTHER_CLAIM_ID),
        ]
    )


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
    # Nothing but the control number used to be here, so now there is nothing.
    assert row.notes is None


@pytest.mark.usefixtures("listeners")
def test_enrollment_reminder_is_a_compliance_item_and_keeps_the_instructions(
    engine: Engine,
) -> None:
    """The one kind that did not move, and why it writes a different row.

    A payer wanting the practice to sign something has no claim and no
    patient. ``app.claims.enrollment`` emits it with a ``claim_id`` of
    ``f"{payer_id}:{transaction_type}"`` — a string that has never named a
    claim — so there is no foreign key to hang it on. It stays where the
    practice's own obligations live, and finds itself again by ``source_ref``.
    """
    register_claim_event_listener(compliance_reminder_listener)

    with _session(engine) as session:
        emit(session, _event("enrollment_action_required"))
        session.commit()

    assert _reminders(engine) == []
    with _session(engine) as session:
        [item] = list(session.execute(select(ComplianceItemRow)).scalars().all())
    assert item.item_type == "claim_enrollment_action_required"
    assert item.label == "Claim PCN20260 enrollment action needed for Aetna"
    assert item.source_ref == _CONTROL_NUMBER
    # The vendor request id is in its own column now, so notes carry only what
    # the payer said — no marker line above it.
    assert item.notes == "Sign and return the EFT authorization form."


@pytest.mark.usefixtures("listeners")
def test_editing_the_notes_does_not_cause_a_second_enrollment_reminder(
    engine: Engine,
) -> None:
    """The bug this column exists to close.

    The reminder used to be found by ``notes`` starting with the request id,
    and the compliance update route replaces ``notes`` wholesale. So a
    clinician writing herself a note where the marker had been severed the only
    link the row had, and the next refresh filed another one — and another on
    every refresh after that.
    """
    register_claim_event_listener(compliance_reminder_listener)

    with _session(engine) as session:
        emit(session, _event("enrollment_action_required"))
        session.commit()

    # She opens the reminder and replaces the note with her own words, exactly
    # as the compliance update route does.
    with _session(engine) as session:
        [item] = list(session.execute(select(ComplianceItemRow)).scalars().all())
        item.notes = "Called Aetna, form is in the post."
        session.commit()

    # The payer still wants the form, so the refresh emits it again.
    with _session(engine) as session:
        emit(session, _event("enrollment_action_required"))
        session.commit()

    with _session(engine) as session:
        items = list(session.execute(select(ComplianceItemRow)).scalars().all())
    assert len(items) == 1
    assert items[0].notes == "Called Aetna, form is in the post."


@pytest.mark.usefixtures("listeners")
def test_a_second_enrollment_reminder_for_the_same_request_is_refused_by_the_database(
    engine: Engine,
) -> None:
    """Not merely unlikely — impossible.

    The listener checks before it writes, which is what stops the duplicate in
    practice. This is the backstop under it: were a second writer to slip past
    that check, the partial unique index refuses the row rather than letting
    the dashboard grow a second copy.
    """
    register_claim_event_listener(compliance_reminder_listener)

    with _session(engine) as session:
        emit(session, _event("enrollment_action_required"))
        session.commit()

    now = datetime(2026, 9, 6, 16, 0, tzinfo=UTC)
    with _session(engine) as session:
        session.add(
            ComplianceItemRow(
                id=str(uuid.uuid4()),
                user_id=_USER_ID,
                item_type="claim_enrollment_action_required",
                label="A second copy of the same request",
                source_ref=_CONTROL_NUMBER,
                due_date=None,
                notes=None,
                completed_at=None,
                created_at=now,
                updated_at=now,
            )
        )
        with pytest.raises(IntegrityError):
            session.commit()


@pytest.mark.usefixtures("listeners")
def test_items_a_person_entered_do_not_collide_with_each_other(engine: Engine) -> None:
    """The index is partial, and this is why.

    A clinician has one licence row, and may well have several of something
    else. None of them is raised by anything, so none has a ``source_ref``, and
    NULLs must not be made to conflict.
    """
    now = datetime(2026, 9, 6, 16, 0, tzinfo=UTC)
    with _session(engine) as session:
        for label in ("BAA — Twilio", "BAA — Google"):
            session.add(
                ComplianceItemRow(
                    id=str(uuid.uuid4()),
                    user_id=_USER_ID,
                    item_type="baa",
                    label=label,
                    source_ref=None,
                    due_date=None,
                    notes=None,
                    completed_at=None,
                    created_at=now,
                    updated_at=now,
                )
            )
        session.commit()

    with _session(engine) as session:
        items = list(session.execute(select(ComplianceItemRow)).scalars().all())
    assert len(items) == 2


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
    assert row.kind == "denied"
    assert row.claim_id == _CLAIM_ID


@pytest.mark.usefixtures("listeners")
def test_find_claim_reminder_misses_another_claim_or_kind(engine: Engine) -> None:
    register_claim_event_listener(compliance_reminder_listener)

    with _session(engine) as session:
        emit(session, _event("denied"))
        session.commit()

    with _session(engine) as session:
        assert (
            find_claim_reminder(session, kind="denied", control_number=_OTHER_CONTROL_NUMBER)
            is None
        )
        assert find_claim_reminder(session, kind="rejected", control_number=_CONTROL_NUMBER) is None


@pytest.mark.usefixtures("listeners")
def test_resolving_completes_the_reminder_the_lookup_returns(engine: Engine) -> None:
    register_claim_event_listener(compliance_reminder_listener)

    with _session(engine) as session:
        emit(session, _event("remittance_held"))
        session.commit()

    with _session(engine) as session:
        assert resolve_compliance_reminder(
            session,
            kind="remittance_held",
            control_number=_CONTROL_NUMBER,
            user_id=_USER_ID,
        )
        session.commit()

    with _session(engine) as session:
        row = find_claim_reminder(session, kind="remittance_held", control_number=_CONTROL_NUMBER)
    assert row is not None
    assert row.completed_at is not None


# --- the vocabulary the column enforces ----------------------------------------


def test_every_actionable_kind_is_one_the_column_accepts() -> None:
    """Two lists of kinds, and they have to agree.

    This used to assert that every kind had a COMPLIANCE TEMPLATE, because a
    reminder was a ``claim_*`` compliance item and a kind with no template
    rendered as nothing on the dashboard. Reminders have their own table now,
    so the list that must agree is the CHECK constraint on ``kind`` — and it
    fails louder than a missing template did: that showed a clinician nothing,
    this refuses the insert.
    """
    expected = tuple(kind for kind in _ALL_KINDS if kind != "paid")

    assert expected == REMINDER_KINDS
    # The claim-backed subset is what the column constrains. The difference
    # between the two lists is exactly the kinds with no claim behind them,
    # which stay compliance items.
    assert set(CLAIM_BACKED_KINDS) == set(CLAIM_REMINDER_KINDS)
    assert set(REMINDER_KINDS) - set(CLAIM_BACKED_KINDS) == set(NON_CLAIM_KINDS)


def test_the_claims_surface_reads_what_the_listener_wrote(engine: Engine) -> None:
    """Billing reads reminders through ``app.claims.reminders``; the rows fit it.

    It reads them with the claim's control number attached, because that is
    the handle a person uses to find the claim in a payer's portal — and
    looking it up per row is how a list becomes N+1 queries.
    """
    from app.claims import reminders  # noqa: PLC0415

    with _session(engine) as session:
        compliance_reminder_listener(session, _event("deadline_missed"))
        session.commit()
        [(row, control_number)] = reminders.list_open(session)

    assert row.kind == "deadline_missed"
    assert control_number == _CONTROL_NUMBER
    assert row.label == "Claim PCN20260 filing deadline missed with Aetna, by 2026-09-01"
