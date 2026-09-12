# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Route tests for GET /api/billing/unbilled-sessions.

The queue's whole point is that "unbilled" is derived, not stored — these
tests exercise exactly the states that derivation has to get right: nothing
finalized yet, a session with no charge at all, one whose only charge
succeeded (must drop out), and one whose only charge failed (must stay, so
it can be retried).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest
from app.main import app
from app.models import Patient
from app.models.coverage import PatientCoverage
from app.models.note import Note
from app.models.session import TherapySession, Transcript
from app.repositories import (
    get_appointment_repository,
    get_appointment_type_repository,
    get_claim_repository,
    get_patient_coverage_repository,
    get_patient_payment_repository,
)
from app.repositories.audit import InMemoryAuditRepository
from app.repositories.claims import InMemoryClaimRepository
from app.repositories.coverage import InMemoryPatientCoverageRepository
from app.routes.billing_queue import UNBILLED_QUEUE_NOTE_LIMIT
from app.scheduling_engine.models.appointment import Appointment, AppointmentStatus
from app.scheduling_engine.models.appointment_type import AppointmentType
from app.scheduling_engine.repositories.appointment import InMemoryAppointmentRepository
from app.scheduling_engine.repositories.appointment_type import InMemoryAppointmentTypeRepository
from app.services import AuditService, get_audit_service

from tests.claims_fixtures import claim as claim_fixture
from tests.claims_fixtures import line as line_fixture

if TYPE_CHECKING:
    from app.repositories import (
        InMemoryNotesRepository,
        InMemoryPatientRepository,
        InMemoryTherapySessionRepository,
    )

PATIENT_ID = "patient-1"


class _FakePayments:
    """Just enough of PatientPaymentRepository for the queue's read.

    Seeded with the succeeded charges as ``(appointment_id, kind)`` pairs,
    exactly what the real query returns — which kind of charge settles a
    visit is the route's judgement, so it is not made here.
    """

    def __init__(self, succeeded: set[tuple[str, str]] | None = None) -> None:
        self._succeeded = succeeded or set()

    def succeeded_charge_kinds(self, appointment_ids: list[str]) -> dict[str, set[str]]:
        kinds: dict[str, set[str]] = {}
        for appointment_id, kind in self._succeeded:
            if appointment_id in appointment_ids:
                kinds.setdefault(appointment_id, set()).add(kind)
        return kinds


def _note(session_id: str, *, finalized: bool) -> Note:
    now = datetime(2026, 6, 1, tzinfo=UTC)
    return Note(
        id=str(uuid.uuid4()),
        patient_id=PATIENT_ID,
        session_id=session_id,
        note_type="soap",
        finalized_at=now if finalized else None,
        created_at=now,
        updated_at=now,
    )


def _session(
    session_id: str, session_date: datetime, *, user_id: str, patient_id: str = PATIENT_ID
) -> TherapySession:
    return TherapySession(
        id=session_id,
        user_id=user_id,
        patient_id=patient_id,
        session_date=session_date,
        session_number=1,
        status="completed",
        transcript=Transcript(format="txt", content="x"),
        created_at=session_date,
    )


def _appointment(appointment_id: str, session_id: str, *, user_id: str) -> Appointment:
    now = datetime(2026, 6, 1, tzinfo=UTC)
    return Appointment(
        id=appointment_id,
        user_id=user_id,
        patient_id=PATIENT_ID,
        title="Session",
        start_at=now,
        end_at=now,
        duration_minutes=50,
        status=AppointmentStatus.CONFIRMED,
        session_type="individual",
        session_id=session_id,
        created_at=now,
        updated_at=now,
    )


@pytest.fixture(autouse=True)
def _clear_overrides():
    yield
    app.dependency_overrides.pop(get_appointment_repository, None)
    app.dependency_overrides.pop(get_appointment_type_repository, None)
    app.dependency_overrides.pop(get_patient_payment_repository, None)
    app.dependency_overrides.pop(get_patient_coverage_repository, None)
    app.dependency_overrides.pop(get_claim_repository, None)
    app.dependency_overrides.pop(get_audit_service, None)


def _wire(
    *,
    appt_repo: InMemoryAppointmentRepository | None = None,
    type_repo: InMemoryAppointmentTypeRepository | None = None,
    payments: _FakePayments | None = None,
    coverage: InMemoryPatientCoverageRepository | None = None,
    claims: InMemoryClaimRepository | None = None,
) -> None:
    app.dependency_overrides[get_appointment_repository] = lambda: (
        appt_repo or InMemoryAppointmentRepository()
    )
    app.dependency_overrides[get_appointment_type_repository] = lambda: (
        type_repo or InMemoryAppointmentTypeRepository()
    )
    app.dependency_overrides[get_patient_payment_repository] = lambda: payments or _FakePayments()
    app.dependency_overrides[get_patient_coverage_repository] = lambda: (
        coverage or InMemoryPatientCoverageRepository()
    )
    app.dependency_overrides[get_claim_repository] = lambda: claims or InMemoryClaimRepository()


def _covered(**overrides: object) -> InMemoryPatientCoverageRepository:
    now = datetime(2026, 6, 1, tzinfo=UTC)
    coverage = InMemoryPatientCoverageRepository()
    coverage.create(
        PatientCoverage(
            id="cov-1",
            patient_id=PATIENT_ID,
            payer_id="payer-1",
            member_id="123456789",
            created_at=now,
            updated_at=now,
            **overrides,
        )
    )
    return coverage


def _271_with_copay(dollars: str) -> dict:
    """A stored eligibility response whose behavioral copay is ``dollars``.

    Only the one benefit line the copay is read off: everything else on a 271
    is either irrelevant to this figure or deliberately not acted on at the
    door.
    """
    return {
        "meta": {"traceId": "trace-1"},
        "benefitsInformation": [
            {
                "code": "B",
                "serviceTypeCodes": ["MH"],
                "timeQualifierCode": "27",
                "benefitAmount": dollars,
            }
        ],
    }


def _claims_on(appointment_id: str, *, state: str, claim_id: str = "claim-1"):
    claims = InMemoryClaimRepository()
    claims.create(
        claim_fixture(
            id=claim_id,
            control_number=claim_id,
            patient_id=PATIENT_ID,
            state=state,
            lines=[line_fixture(claim_id=claim_id, appointment_id=appointment_id)],
        )
    )
    return claims


def _seed_patient(
    mock_repo: InMemoryPatientRepository, mock_user_id: str, *, rate_cents: int
) -> None:
    now = datetime.now(UTC)
    mock_repo.create(
        Patient(
            id=PATIENT_ID,
            first_name="Ada",
            last_name="Early",
            created_at=now,
            updated_at=now,
            rate_cents=rate_cents,
        ),
        mock_user_id,
    )


def _seed_visit(
    mock_repo: InMemoryPatientRepository,
    mock_session_repo: InMemoryTherapySessionRepository,
    mock_notes_repo: InMemoryNotesRepository,
    mock_user_id: str,
    *,
    patient_id: str,
    session_id: str,
    finalized_at: datetime,
    rate_cents: int = 15000,
) -> None:
    """A finalized, unbilled visit for a given client — the batch tests' unit."""
    mock_repo.create(
        Patient(
            id=patient_id,
            first_name="Client",
            last_name=patient_id,
            created_at=finalized_at,
            updated_at=finalized_at,
            rate_cents=rate_cents,
        ),
        mock_user_id,
    )
    mock_session_repo.create(
        _session(session_id, finalized_at, user_id=mock_user_id, patient_id=patient_id)
    )
    mock_notes_repo.add(
        Note(
            id=str(uuid.uuid4()),
            patient_id=patient_id,
            session_id=session_id,
            note_type="soap",
            finalized_at=finalized_at,
            created_at=finalized_at,
            updated_at=finalized_at,
        ),
        mock_user_id,
    )


class _CountingCoverageRepo:
    """Wraps a coverage repository, counting how each lookup method is used.

    Lets a test assert the route takes the batch path instead of the
    per-patient one without caring what the underlying data looks like.
    """

    def __init__(self, inner: InMemoryPatientCoverageRepository) -> None:
        self._inner = inner
        self.get_active_calls = 0
        self.get_active_for_patients_calls = 0

    def get(self, coverage_id: str) -> PatientCoverage | None:
        return self._inner.get(coverage_id)

    def get_active(self, patient_id: str) -> PatientCoverage | None:
        self.get_active_calls += 1
        return self._inner.get_active(patient_id)

    def get_active_for_patients(self, patient_ids: list[str]) -> dict[str, PatientCoverage]:
        self.get_active_for_patients_calls += 1
        return self._inner.get_active_for_patients(patient_ids)

    def create(self, coverage: PatientCoverage) -> PatientCoverage:
        return self._inner.create(coverage)

    def update(self, coverage: PatientCoverage) -> PatientCoverage:
        return self._inner.update(coverage)


def test_empty_queue_when_nothing_finalized(client) -> None:
    _wire()
    resp = client.get("/api/billing/unbilled-sessions")
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"items": []}


def test_populated_queue_shows_client_date_and_resolved_amount(
    client,
    mock_repo: InMemoryPatientRepository,
    mock_session_repo: InMemoryTherapySessionRepository,
    mock_notes_repo: InMemoryNotesRepository,
    mock_user_id: str,
) -> None:
    _seed_patient(mock_repo, mock_user_id, rate_cents=15000)
    session_date = datetime(2026, 6, 10, 14, 0, tzinfo=UTC)
    mock_session_repo.create(_session("sess-1", session_date, user_id=mock_user_id))
    mock_notes_repo.add(_note("sess-1", finalized=True), mock_user_id)
    _wire()

    resp = client.get("/api/billing/unbilled-sessions")
    assert resp.status_code == 200, resp.text
    items = resp.json()["items"]
    assert len(items) == 1
    assert items[0]["session_id"] == "sess-1"
    assert items[0]["patient_id"] == PATIENT_ID
    assert items[0]["patient_name"] == "Ada Early"
    assert items[0]["amount_cents"] == 15000


def test_unfinalized_note_is_not_in_the_queue(
    client,
    mock_repo: InMemoryPatientRepository,
    mock_session_repo: InMemoryTherapySessionRepository,
    mock_notes_repo: InMemoryNotesRepository,
    mock_user_id: str,
) -> None:
    _seed_patient(mock_repo, mock_user_id, rate_cents=15000)
    mock_session_repo.create(
        _session("sess-1", datetime(2026, 6, 10, tzinfo=UTC), user_id=mock_user_id)
    )
    mock_notes_repo.add(_note("sess-1", finalized=False), mock_user_id)
    _wire()

    resp = client.get("/api/billing/unbilled-sessions")
    assert resp.json()["items"] == []


def test_succeeded_charge_drops_the_session_from_the_queue(
    client,
    mock_repo: InMemoryPatientRepository,
    mock_session_repo: InMemoryTherapySessionRepository,
    mock_notes_repo: InMemoryNotesRepository,
    mock_user_id: str,
) -> None:
    _seed_patient(mock_repo, mock_user_id, rate_cents=15000)
    mock_session_repo.create(
        _session("sess-1", datetime(2026, 6, 10, tzinfo=UTC), user_id=mock_user_id)
    )
    mock_notes_repo.add(_note("sess-1", finalized=True), mock_user_id)
    appt_repo = InMemoryAppointmentRepository()
    appt_repo.create(_appointment("appt-1", "sess-1", user_id=mock_user_id))
    _wire(appt_repo=appt_repo, payments=_FakePayments(succeeded={("appt-1", "session")}))

    resp = client.get("/api/billing/unbilled-sessions")
    assert resp.json()["items"] == []


def test_failed_charge_keeps_the_session_in_the_queue(
    client,
    mock_repo: InMemoryPatientRepository,
    mock_session_repo: InMemoryTherapySessionRepository,
    mock_notes_repo: InMemoryNotesRepository,
    mock_user_id: str,
) -> None:
    _seed_patient(mock_repo, mock_user_id, rate_cents=15000)
    mock_session_repo.create(
        _session("sess-1", datetime(2026, 6, 10, tzinfo=UTC), user_id=mock_user_id)
    )
    mock_notes_repo.add(_note("sess-1", finalized=True), mock_user_id)
    appt_repo = InMemoryAppointmentRepository()
    appt_repo.create(_appointment("appt-1", "sess-1", user_id=mock_user_id))
    # A failed charge exists but is not a *succeeded* one — the fake payments
    # repo (like the real one) only ever reports succeeded appointment ids,
    # so a failed-only appointment simply never appears in that set.
    _wire(appt_repo=appt_repo, payments=_FakePayments(succeeded=set()))

    resp = client.get("/api/billing/unbilled-sessions")
    items = resp.json()["items"]
    assert len(items) == 1
    assert items[0]["session_id"] == "sess-1"


def test_amount_falls_back_to_appointment_type_default(
    client,
    mock_repo: InMemoryPatientRepository,
    mock_session_repo: InMemoryTherapySessionRepository,
    mock_notes_repo: InMemoryNotesRepository,
    mock_user_id: str,
) -> None:
    _seed_patient(mock_repo, mock_user_id, rate_cents=None)
    mock_session_repo.create(
        _session("sess-1", datetime(2026, 6, 10, tzinfo=UTC), user_id=mock_user_id)
    )
    mock_notes_repo.add(_note("sess-1", finalized=True), mock_user_id)
    appt = _appointment("appt-1", "sess-1", user_id=mock_user_id)
    appt.appointment_type_id = "type-1"
    appt_repo = InMemoryAppointmentRepository()
    appt_repo.create(appt)
    type_repo = InMemoryAppointmentTypeRepository()
    type_repo.create(
        AppointmentType(
            id="type-1", user_id=mock_user_id, name="Individual", default_fee_cents=12000
        )
    )
    _wire(appt_repo=appt_repo, type_repo=type_repo)

    resp = client.get("/api/billing/unbilled-sessions")
    items = resp.json()["items"]
    assert items[0]["amount_cents"] == 12000


def test_row_says_whether_the_client_has_coverage(
    client,
    mock_repo: InMemoryPatientRepository,
    mock_session_repo: InMemoryTherapySessionRepository,
    mock_notes_repo: InMemoryNotesRepository,
    mock_user_id: str,
) -> None:
    _seed_patient(mock_repo, mock_user_id, rate_cents=15000)
    mock_session_repo.create(
        _session("sess-1", datetime(2026, 6, 10, tzinfo=UTC), user_id=mock_user_id)
    )
    mock_notes_repo.add(_note("sess-1", finalized=True), mock_user_id)
    appt_repo = InMemoryAppointmentRepository()
    appt_repo.create(_appointment("appt-1", "sess-1", user_id=mock_user_id))

    _wire(appt_repo=appt_repo)
    row = client.get("/api/billing/unbilled-sessions").json()["items"][0]
    assert row["has_coverage"] is False
    assert row["appointment_id"] == "appt-1"
    assert row["claim"] is None

    _wire(appt_repo=appt_repo, coverage=_covered())
    row = client.get("/api/billing/unbilled-sessions").json()["items"][0]
    assert row["has_coverage"] is True


def test_row_carries_the_newest_claim_on_the_visit(
    client,
    mock_repo: InMemoryPatientRepository,
    mock_session_repo: InMemoryTherapySessionRepository,
    mock_notes_repo: InMemoryNotesRepository,
    mock_user_id: str,
) -> None:
    _seed_patient(mock_repo, mock_user_id, rate_cents=15000)
    mock_session_repo.create(
        _session("sess-1", datetime(2026, 6, 10, tzinfo=UTC), user_id=mock_user_id)
    )
    mock_notes_repo.add(_note("sess-1", finalized=True), mock_user_id)
    appt_repo = InMemoryAppointmentRepository()
    appt_repo.create(_appointment("appt-1", "sess-1", user_id=mock_user_id))
    _wire(appt_repo=appt_repo, coverage=_covered(), claims=_claims_on("appt-1", state="submitted"))

    row = client.get("/api/billing/unbilled-sessions").json()["items"][0]
    assert row["claim"] == {
        "id": "claim-1",
        "control_number": "claim-1",
        "state": "submitted",
        "frequency_code": "1",
    }


def test_paid_claim_drops_the_session_from_the_queue(
    client,
    mock_repo: InMemoryPatientRepository,
    mock_session_repo: InMemoryTherapySessionRepository,
    mock_notes_repo: InMemoryNotesRepository,
    mock_user_id: str,
) -> None:
    _seed_patient(mock_repo, mock_user_id, rate_cents=15000)
    mock_session_repo.create(
        _session("sess-1", datetime(2026, 6, 10, tzinfo=UTC), user_id=mock_user_id)
    )
    mock_notes_repo.add(_note("sess-1", finalized=True), mock_user_id)
    appt_repo = InMemoryAppointmentRepository()
    appt_repo.create(_appointment("appt-1", "sess-1", user_id=mock_user_id))
    _wire(appt_repo=appt_repo, coverage=_covered(), claims=_claims_on("appt-1", state="paid"))

    assert client.get("/api/billing/unbilled-sessions").json()["items"] == []


def test_denied_claim_keeps_the_session_in_the_queue(
    client,
    mock_repo: InMemoryPatientRepository,
    mock_session_repo: InMemoryTherapySessionRepository,
    mock_notes_repo: InMemoryNotesRepository,
    mock_user_id: str,
) -> None:
    _seed_patient(mock_repo, mock_user_id, rate_cents=15000)
    mock_session_repo.create(
        _session("sess-1", datetime(2026, 6, 10, tzinfo=UTC), user_id=mock_user_id)
    )
    mock_notes_repo.add(_note("sess-1", finalized=True), mock_user_id)
    appt_repo = InMemoryAppointmentRepository()
    appt_repo.create(_appointment("appt-1", "sess-1", user_id=mock_user_id))
    _wire(appt_repo=appt_repo, coverage=_covered(), claims=_claims_on("appt-1", state="denied"))

    items = client.get("/api/billing/unbilled-sessions").json()["items"]
    assert len(items) == 1
    assert items[0]["claim"]["state"] == "denied"


# ---------------------------------------------------------------------------
# The copay the row offers to collect
# ---------------------------------------------------------------------------


@pytest.fixture
def one_finalized_visit(
    mock_repo: InMemoryPatientRepository,
    mock_session_repo: InMemoryTherapySessionRepository,
    mock_notes_repo: InMemoryNotesRepository,
    mock_user_id: str,
) -> InMemoryAppointmentRepository:
    """One finalized, booked, unbilled session for a client with a rate."""
    _seed_patient(mock_repo, mock_user_id, rate_cents=15000)
    mock_session_repo.create(
        _session("sess-1", datetime(2026, 6, 10, tzinfo=UTC), user_id=mock_user_id)
    )
    mock_notes_repo.add(_note("sess-1", finalized=True), mock_user_id)
    appt_repo = InMemoryAppointmentRepository()
    appt_repo.create(_appointment("appt-1", "sess-1", user_id=mock_user_id))
    return appt_repo


def test_copay_comes_from_the_practices_override(client, one_finalized_visit) -> None:
    _wire(appt_repo=one_finalized_visit, coverage=_covered(copay_override_cents=3000))

    row = client.get("/api/billing/unbilled-sessions").json()["items"][0]
    assert row["copay_cents"] == 3000


def test_copay_falls_back_to_what_the_payer_last_said(client, one_finalized_visit) -> None:
    coverage = _covered(
        last_271=_271_with_copay("25"), verified_at=datetime(2026, 6, 5, tzinfo=UTC)
    )
    _wire(appt_repo=one_finalized_visit, coverage=coverage)

    row = client.get("/api/billing/unbilled-sessions").json()["items"][0]
    assert row["copay_cents"] == 2500


def test_the_override_wins_over_the_payers_answer(client, one_finalized_visit) -> None:
    coverage = _covered(
        copay_override_cents=3000,
        last_271=_271_with_copay("25"),
        verified_at=datetime(2026, 6, 5, tzinfo=UTC),
    )
    _wire(appt_repo=one_finalized_visit, coverage=coverage)

    row = client.get("/api/billing/unbilled-sessions").json()["items"][0]
    assert row["copay_cents"] == 3000


def test_no_override_and_no_answer_leaves_the_copay_unknown(client, one_finalized_visit) -> None:
    # Unknown, not zero: the row asks for the amount rather than offering to
    # charge a figure nobody chose. Nothing else on a 271 stands in for it.
    _wire(appt_repo=one_finalized_visit, coverage=_covered())

    row = client.get("/api/billing/unbilled-sessions").json()["items"][0]
    assert row["copay_cents"] is None


def test_an_uncovered_client_has_no_copay(client, one_finalized_visit) -> None:
    _wire(appt_repo=one_finalized_visit)

    row = client.get("/api/billing/unbilled-sessions").json()["items"][0]
    assert row["has_coverage"] is False
    assert row["copay_cents"] is None


def test_a_collected_copay_keeps_the_session_in_the_queue(client, one_finalized_visit) -> None:
    # The copay is a part payment on a visit the payer has yet to be billed
    # for: filing the claim is what the row is still there to do.
    _wire(
        appt_repo=one_finalized_visit,
        coverage=_covered(copay_override_cents=3000),
        payments=_FakePayments(succeeded={("appt-1", "copay")}),
    )

    items = client.get("/api/billing/unbilled-sessions").json()["items"]
    assert len(items) == 1
    assert items[0]["session_id"] == "sess-1"


# ---------------------------------------------------------------------------
# Batching: one coverage query and one audit row for the whole queue, not
# one per row.
# ---------------------------------------------------------------------------


def test_coverage_lookup_is_batched_across_patients(
    client,
    mock_repo: InMemoryPatientRepository,
    mock_session_repo: InMemoryTherapySessionRepository,
    mock_notes_repo: InMemoryNotesRepository,
    mock_user_id: str,
) -> None:
    now = datetime(2026, 6, 1, tzinfo=UTC)
    for i in range(1, 4):
        _seed_visit(
            mock_repo,
            mock_session_repo,
            mock_notes_repo,
            mock_user_id,
            patient_id=f"patient-{i}",
            session_id=f"sess-{i}",
            finalized_at=now,
        )
    coverage = _CountingCoverageRepo(InMemoryPatientCoverageRepository())
    _wire(coverage=coverage)

    resp = client.get("/api/billing/unbilled-sessions")

    assert len(resp.json()["items"]) == 3
    assert coverage.get_active_for_patients_calls == 1
    assert coverage.get_active_calls == 0


def test_exactly_one_audit_row_per_request_naming_the_session_ids(
    client,
    mock_repo: InMemoryPatientRepository,
    mock_session_repo: InMemoryTherapySessionRepository,
    mock_notes_repo: InMemoryNotesRepository,
    mock_user_id: str,
) -> None:
    now = datetime(2026, 6, 1, tzinfo=UTC)
    session_ids = [f"sess-{i}" for i in range(1, 4)]
    for i, session_id in zip(range(1, 4), session_ids, strict=True):
        _seed_visit(
            mock_repo,
            mock_session_repo,
            mock_notes_repo,
            mock_user_id,
            patient_id=f"patient-{i}",
            session_id=session_id,
            finalized_at=now,
        )
    repository = InMemoryAuditRepository()
    app.dependency_overrides[get_audit_service] = lambda: AuditService(repository)
    _wire()

    resp = client.get("/api/billing/unbilled-sessions")

    assert len(resp.json()["items"]) == 3
    logged = repository.list_for_user(mock_user_id)
    session_viewed = [entry for entry in logged if entry.action == "session_viewed"]
    assert len(session_viewed) == 1
    assert sorted(session_viewed[0].changes["session_ids"]) == sorted(session_ids)
    assert session_viewed[0].changes["count"] == 3
    # ids only — no patient name, no clinical content.
    assert "patient_name" not in session_viewed[0].changes


def test_list_finalized_is_called_with_the_queue_limit(
    client,
    mock_repo: InMemoryPatientRepository,
    mock_session_repo: InMemoryTherapySessionRepository,
    mock_notes_repo: InMemoryNotesRepository,
    mock_user_id: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert UNBILLED_QUEUE_NOTE_LIMIT == 500
    _seed_patient(mock_repo, mock_user_id, rate_cents=15000)
    mock_session_repo.create(
        _session("sess-1", datetime(2026, 6, 10, tzinfo=UTC), user_id=mock_user_id)
    )
    mock_notes_repo.add(_note("sess-1", finalized=True), mock_user_id)
    calls: list[int | None] = []
    original_list_finalized = mock_notes_repo.list_finalized

    def _spy(user_id: str, *, limit: int | None = None) -> list[Note]:
        calls.append(limit)
        return original_list_finalized(user_id, limit=limit)

    monkeypatch.setattr(mock_notes_repo, "list_finalized", _spy)
    _wire()

    client.get("/api/billing/unbilled-sessions")

    assert calls == [UNBILLED_QUEUE_NOTE_LIMIT]


def test_queue_rows_and_order_are_unchanged_by_batching(
    client,
    mock_repo: InMemoryPatientRepository,
    mock_session_repo: InMemoryTherapySessionRepository,
    mock_notes_repo: InMemoryNotesRepository,
    mock_user_id: str,
) -> None:
    """A settled visit, a claimed visit, and a plain unbilled one, together.

    Pins down that batching the coverage lookup and hoisting the audit call
    didn't change which rows come back or their order — newest finalized
    note first, settled visits dropped.
    """
    _seed_patient(mock_repo, mock_user_id, rate_cents=15000)
    oldest = datetime(2026, 6, 1, tzinfo=UTC)
    middle = datetime(2026, 6, 5, tzinfo=UTC)
    newest = datetime(2026, 6, 10, tzinfo=UTC)

    mock_session_repo.create(_session("sess-settled", oldest, user_id=mock_user_id))
    mock_notes_repo.add(
        Note(
            id=str(uuid.uuid4()),
            patient_id=PATIENT_ID,
            session_id="sess-settled",
            note_type="soap",
            finalized_at=oldest,
            created_at=oldest,
            updated_at=oldest,
        ),
        mock_user_id,
    )
    mock_session_repo.create(_session("sess-claimed", middle, user_id=mock_user_id))
    mock_notes_repo.add(
        Note(
            id=str(uuid.uuid4()),
            patient_id=PATIENT_ID,
            session_id="sess-claimed",
            note_type="soap",
            finalized_at=middle,
            created_at=middle,
            updated_at=middle,
        ),
        mock_user_id,
    )
    mock_session_repo.create(_session("sess-unbilled", newest, user_id=mock_user_id))
    mock_notes_repo.add(
        Note(
            id=str(uuid.uuid4()),
            patient_id=PATIENT_ID,
            session_id="sess-unbilled",
            note_type="soap",
            finalized_at=newest,
            created_at=newest,
            updated_at=newest,
        ),
        mock_user_id,
    )

    appt_repo = InMemoryAppointmentRepository()
    appt_repo.create(_appointment("appt-settled", "sess-settled", user_id=mock_user_id))
    appt_repo.create(_appointment("appt-claimed", "sess-claimed", user_id=mock_user_id))
    _wire(
        appt_repo=appt_repo,
        payments=_FakePayments(succeeded={("appt-settled", "session")}),
        coverage=_covered(),
        claims=_claims_on("appt-claimed", state="submitted"),
    )

    items = client.get("/api/billing/unbilled-sessions").json()["items"]

    assert [item["session_id"] for item in items] == ["sess-unbilled", "sess-claimed"]
    assert items[1]["claim"]["state"] == "submitted"
