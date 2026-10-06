# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Starting a recorded session respects the client's answer about AI-assisted notes.

Both start paths — ``POST /api/appointments/{id}/start-session`` and
``POST /api/sessions/schedule`` — with the practice asking its clients:

* a client who declined is refused with ``CLIENT_DECLINED_AI_NOTES`` and the
  day they declined, nothing is created, and the refusal is audited;
* a client nobody has asked yet, or who agreed, starts;
* "Client agreed today" — a clinician entry dated today, then the start —
  writes one entry and one audit row, and the session starts;
* a session started to write its note by hand (``recording: false``) is not
  refused;
* a client the caller cannot see is 404 before the consent record is read.

With the practice not asking, a decline on the chart stops nothing.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime
from typing import TYPE_CHECKING, Any
from unittest.mock import MagicMock

import pytest
from app.main import app
from app.models import Patient, SessionStatus
from app.models.audit import AuditAction
from app.models.session import TherapySession, Transcript
from app.repositories import get_client_ai_consent_repository, get_patient_repository
from app.repositories.audit import InMemoryAuditRepository
from app.repositories.client_ai_consent import InMemoryClientAiConsentRepository
from app.routes.scheduling import (
    _get_session_service,
    get_owner_timezone,
    get_scheduling_service,
)
from app.services import AuditService, get_audit_service
from app.services.client_ai_consent import record_ai_consent
from app.services.recording_consent import (
    CLIENT_DECLINED_AI_NOTES,
    RecordingConsentGate,
    get_recording_consent_gate,
)

if TYPE_CHECKING:
    from app.models.client_ai_consent import AiConsentEvent
    from app.repositories import InMemoryPatientRepository
    from fastapi.testclient import TestClient

_DECLINED_ON = date(2026, 9, 14)


class _UnreadableConsents(InMemoryClientAiConsentRepository):
    """Fails the test if anything reads the consent record."""

    def list_for_patient(self, patient_id: str) -> list[AiConsentEvent]:
        raise AssertionError("the consent record was read before the client was looked up")


@pytest.fixture
def consents() -> InMemoryClientAiConsentRepository:
    return InMemoryClientAiConsentRepository()


@pytest.fixture
def audit() -> AuditService:
    return AuditService(InMemoryAuditRepository())


@pytest.fixture
def patient(mock_repo: InMemoryPatientRepository, mock_user_id: str) -> Patient:
    now = datetime.now(UTC)
    created = Patient(
        id=str(uuid.uuid4()),
        first_name="Jane",
        last_name="Smith",
        created_at=now,
        updated_at=now,
    )
    return mock_repo.create(created, mock_user_id)


def _ask_clients(
    consents: InMemoryClientAiConsentRepository, audit: AuditService, *, asks: bool = True
) -> None:
    app.dependency_overrides[get_recording_consent_gate] = lambda: RecordingConsentGate(
        asks_clients=asks, consents=consents
    )
    app.dependency_overrides[get_client_ai_consent_repository] = lambda: consents
    app.dependency_overrides[get_audit_service] = lambda: audit


def _decline(consents: InMemoryClientAiConsentRepository, patient_id: str, user_id: str) -> None:
    record_ai_consent(patient_id, "declined", _DECLINED_ON, "clinician", user_id, repo=consents)


def _audited(audit: AuditService, user_id: str, action: AuditAction) -> list[Any]:
    rows = audit._repo.list_for_user(user_id)  # type: ignore[attr-defined]  # in-memory audit repo exposes its rows for tests
    return [r for r in rows if r.action == action]


def _assert_declined(response: Any) -> None:
    assert response.status_code == 403, response.text
    error = response.json()["error"]
    assert error["code"] == CLIENT_DECLINED_AI_NOTES
    assert error["details"] == {"declined_on": _DECLINED_ON.isoformat()}


# --- POST /api/appointments/{id}/start-session --------------------------------


def _appointment(patient_id: str) -> MagicMock:
    appt = MagicMock()
    appt.session_id = None
    appt.patient_id = patient_id
    appt.start_at = datetime(2026, 10, 5, 14, 0, tzinfo=UTC)
    appt.duration_minutes = 50
    appt.video_link = None
    appt.video_platform = None
    appt.session_type = "individual"
    appt.notes = None
    appt.note_type = None
    appt.note_inputs = None
    return appt


def _session(patient_id: str, user_id: str) -> TherapySession:
    when = datetime(2026, 10, 5, 14, 0, tzinfo=UTC)
    return TherapySession(
        id="session-1",
        user_id=user_id,
        patient_id=patient_id,
        session_date=when,
        session_number=1,
        status=SessionStatus.SCHEDULED,
        transcript=Transcript(format="txt", content=""),
        created_at=when,
        scheduled_at=when,
        duration_minutes=50,
        session_type="individual",
        source="companion",
    )


def _wire_start_session(
    patient: Patient | None, patient_id: str, user_id: str
) -> tuple[MagicMock, MagicMock]:
    scheduling_svc = MagicMock()
    scheduling_svc.get_appointment.return_value = _appointment(patient_id)
    session_svc = MagicMock()
    session_svc.patient_repo.get.return_value = patient
    session_svc.schedule_session.return_value = (_session(patient_id, user_id), patient)
    app.dependency_overrides[get_scheduling_service] = lambda: scheduling_svc
    app.dependency_overrides[_get_session_service] = lambda: session_svc
    return scheduling_svc, session_svc


class TestStartSessionFromAppointment:
    _URL = "/api/appointments/appt-1/start-session"

    def test_declined_is_refused_audited_and_creates_nothing(
        self,
        client: TestClient,
        patient: Patient,
        consents: InMemoryClientAiConsentRepository,
        audit: AuditService,
        mock_user_id: str,
    ) -> None:
        _ask_clients(consents, audit)
        _decline(consents, patient.id, mock_user_id)
        scheduling_svc, session_svc = _wire_start_session(patient, patient.id, mock_user_id)

        response = client.post(self._URL, json={})

        _assert_declined(response)
        session_svc.schedule_session.assert_not_called()
        scheduling_svc.update_appointment.assert_not_called()
        refusals = _audited(audit, mock_user_id, AuditAction.PATIENT_AI_CONSENT_VIEWED)
        assert len(refusals) == 1
        assert refusals[0].patient_id == patient.id
        assert refusals[0].changes == {"recording_refused": True}

    def test_declined_is_refused_with_no_body(
        self,
        client: TestClient,
        patient: Patient,
        consents: InMemoryClientAiConsentRepository,
        audit: AuditService,
        mock_user_id: str,
    ) -> None:
        """The companion starts with no body at all; that is a recording."""
        _ask_clients(consents, audit)
        _decline(consents, patient.id, mock_user_id)
        _wire_start_session(patient, patient.id, mock_user_id)

        _assert_declined(client.post(self._URL))

    def test_a_note_written_by_hand_starts_despite_a_decline(
        self,
        client: TestClient,
        patient: Patient,
        consents: InMemoryClientAiConsentRepository,
        audit: AuditService,
        mock_user_id: str,
    ) -> None:
        _ask_clients(consents, audit)
        _decline(consents, patient.id, mock_user_id)
        _, session_svc = _wire_start_session(patient, patient.id, mock_user_id)

        response = client.post(self._URL, json={"recording": False})

        assert response.status_code == 201, response.text
        session_svc.schedule_session.assert_called_once()

    @pytest.mark.parametrize("answer", [None, "consented"])
    def test_not_asked_or_agreed_starts(
        self,
        client: TestClient,
        patient: Patient,
        consents: InMemoryClientAiConsentRepository,
        audit: AuditService,
        mock_user_id: str,
        answer: str | None,
    ) -> None:
        _ask_clients(consents, audit)
        if answer == "consented":
            record_ai_consent(
                patient.id, "consented", _DECLINED_ON, "clinician", mock_user_id, repo=consents
            )
        _wire_start_session(patient, patient.id, mock_user_id)

        response = client.post(self._URL, json={})

        assert response.status_code == 201, response.text
        assert len(consents.events) == (1 if answer else 0)

    def test_practice_not_asking_ignores_a_decline(
        self,
        client: TestClient,
        patient: Patient,
        consents: InMemoryClientAiConsentRepository,
        audit: AuditService,
        mock_user_id: str,
    ) -> None:
        _ask_clients(consents, audit, asks=False)
        _decline(consents, patient.id, mock_user_id)
        _wire_start_session(patient, patient.id, mock_user_id)

        assert client.post(self._URL, json={}).status_code == 201

    def test_unseen_client_is_404_before_the_consent_record_is_read(
        self,
        client: TestClient,
        audit: AuditService,
        mock_user_id: str,
    ) -> None:
        _ask_clients(_UnreadableConsents(), audit)
        _, session_svc = _wire_start_session(None, "someone-else", mock_user_id)

        response = client.post(self._URL, json={})

        assert response.status_code == 404, response.text
        session_svc.schedule_session.assert_not_called()


# --- POST /api/sessions/schedule ----------------------------------------------


def _schedule(client: TestClient, patient_id: str) -> Any:
    return client.post(
        "/api/sessions/schedule",
        json={
            "patient_id": patient_id,
            "scheduled_at": "2026-10-05T14:00:00Z",
            "source": "companion",
        },
    )


class TestScheduleSession:
    def test_declined_is_refused_and_creates_nothing(
        self,
        client: TestClient,
        patient: Patient,
        consents: InMemoryClientAiConsentRepository,
        audit: AuditService,
        mock_user_id: str,
    ) -> None:
        _ask_clients(consents, audit)
        _decline(consents, patient.id, mock_user_id)

        _assert_declined(_schedule(client, patient.id))

        assert client.get("/api/sessions").json()["data"] == []
        assert _audited(audit, mock_user_id, AuditAction.SESSION_CREATED) == []

    def test_not_asked_starts_and_writes_nothing(
        self,
        client: TestClient,
        patient: Patient,
        consents: InMemoryClientAiConsentRepository,
        audit: AuditService,
    ) -> None:
        _ask_clients(consents, audit)

        assert _schedule(client, patient.id).status_code == 201
        assert consents.events == []

    def test_client_agreed_today_writes_one_entry_then_starts(
        self,
        client: TestClient,
        patient: Patient,
        consents: InMemoryClientAiConsentRepository,
        audit: AuditService,
        mock_repo: InMemoryPatientRepository,
        mock_user_id: str,
    ) -> None:
        """The prompt's "Client agreed today": the chart's own POST with no
        date, then the start."""
        _ask_clients(consents, audit)
        app.dependency_overrides[get_patient_repository] = lambda: mock_repo
        app.dependency_overrides[get_owner_timezone] = lambda: UTC

        recorded = client.post(
            f"/api/patients/{patient.id}/ai-consent", json={"decision": "consented"}
        )
        assert recorded.status_code == 201, recorded.text
        started = _schedule(client, patient.id)

        assert started.status_code == 201, started.text
        assert len(consents.events) == 1
        entry = consents.events[0]
        assert (entry.decision, entry.source, entry.recorded_by) == (
            "consented",
            "clinician",
            mock_user_id,
        )
        assert entry.effective_on == datetime.now(UTC).date()
        rows = _audited(audit, mock_user_id, AuditAction.PATIENT_AI_CONSENT_RECORDED)
        assert len(rows) == 1
        assert rows[0].patient_id == patient.id
        assert rows[0].changes["decision"] == "consented"

    def test_practice_not_asking_ignores_a_decline(
        self,
        client: TestClient,
        patient: Patient,
        consents: InMemoryClientAiConsentRepository,
        audit: AuditService,
        mock_user_id: str,
    ) -> None:
        _ask_clients(consents, audit, asks=False)
        _decline(consents, patient.id, mock_user_id)

        assert _schedule(client, patient.id).status_code == 201

    def test_unseen_client_is_404_before_the_consent_record_is_read(
        self, client: TestClient, audit: AuditService
    ) -> None:
        _ask_clients(_UnreadableConsents(), audit)

        response = _schedule(client, str(uuid.uuid4()))

        assert response.status_code == 404, response.text
        assert response.json()["error"]["code"] == "PATIENT_NOT_FOUND"
