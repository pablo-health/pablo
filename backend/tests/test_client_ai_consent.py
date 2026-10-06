# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Unit tests for a client's answer about AI-assisted notes.

The record is a history, not a field: recording "declined" after "agreed"
leaves both on the chart, in order, with the later one current. Covered here,
against the service and then the route:

* agreed then declined reads current=declined with a two-entry history, and
  the first entry is untouched;
* a future date is refused (400 on the route), and an omitted one is today in
  the clinician's own timezone;
* the source rules: a clinician's entry names who recorded it, an intake-form
  entry names its submission;
* every POST writes one audit row naming the patient and the decision, and
  every GET is audited too;
* a client the caller cannot see is 404, and nothing is written.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta, tzinfo
from zoneinfo import ZoneInfo

import pytest
from app.auth.service import require_baa_acceptance
from app.models import User
from app.models.patient import Patient
from app.repositories import get_client_ai_consent_repository, get_patient_repository
from app.repositories.audit import InMemoryAuditRepository
from app.repositories.client_ai_consent import InMemoryClientAiConsentRepository
from app.routes import client_ai_consent
from app.routes.scheduling import get_owner_timezone
from app.services import AuditService, get_audit_service
from app.services.client_ai_consent import (
    FutureAiConsentDateError,
    ai_consent_record,
    record_ai_consent,
)
from fastapi import FastAPI
from fastapi.testclient import TestClient

_USER_ID = "user-1"
_PATIENT_ID = "11111111-1111-4111-8111-111111111111"
_OTHER_PATIENT_ID = "22222222-2222-4222-8222-222222222222"
_URL = f"/api/patients/{_PATIENT_ID}/ai-consent"
# A "today" after every date the service tests record, so none reads as future.
_LATER = date(2026, 12, 1)


class _FakePatients:
    def __init__(self, *, visible: bool = True) -> None:
        self.visible = visible

    def get(self, patient_id: str, user_id: str) -> Patient | None:
        if not self.visible or patient_id != _PATIENT_ID or user_id != _USER_ID:
            return None
        return Patient(
            id=_PATIENT_ID,
            first_name="A",
            last_name="B",
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )


def _user() -> User:
    return User(
        id=_USER_ID,
        email="therapist@example.com",
        name="Test Therapist",
        created_at=datetime.now(UTC),
        baa_accepted_at=datetime.now(UTC),
        baa_version="2024-01-01",
    )


def _client(
    repo: InMemoryClientAiConsentRepository,
    *,
    visible: bool = True,
    tz: tzinfo = UTC,
) -> tuple[TestClient, AuditService]:
    app = FastAPI()
    app.include_router(client_ai_consent.router)
    app.dependency_overrides[require_baa_acceptance] = _user
    app.dependency_overrides[get_patient_repository] = lambda: _FakePatients(visible=visible)
    app.dependency_overrides[get_client_ai_consent_repository] = lambda: repo
    app.dependency_overrides[get_owner_timezone] = lambda: tz
    audit = AuditService(InMemoryAuditRepository())
    app.dependency_overrides[get_audit_service] = lambda: audit
    return TestClient(app, raise_server_exceptions=False), audit


def _actions(audit: AuditService, action: str) -> list:  # type: ignore[type-arg]
    return [e for e in audit._repo.list_for_user(_USER_ID) if e.action == action]  # type: ignore[attr-defined]  # in-memory audit repo exposes its rows for tests


class TestService:
    def test_agreed_then_declined_is_declined_with_both_in_order(self) -> None:
        repo = InMemoryClientAiConsentRepository()
        first = record_ai_consent(
            _PATIENT_ID,
            "consented",
            date(2026, 10, 6),
            "clinician",
            _USER_ID,
            today=_LATER,
            repo=repo,
        )
        record_ai_consent(
            _PATIENT_ID,
            "declined",
            date(2026, 11, 2),
            "clinician",
            _USER_ID,
            today=_LATER,
            repo=repo,
        )

        record = ai_consent_record(_PATIENT_ID, repo=repo)

        assert record.current is not None
        assert record.current.decision == "declined"
        assert [(e.decision, e.effective_on) for e in record.history] == [
            ("consented", date(2026, 10, 6)),
            ("declined", date(2026, 11, 2)),
        ]
        # Appended, not updated: the first answer is still exactly as written.
        assert repo.events[0] == first

    def test_current_is_the_latest_recorded_not_the_latest_dated(self) -> None:
        """A clinician catching up may record an older answer last; the most
        recently recorded one is still the client's current word."""
        repo = InMemoryClientAiConsentRepository()
        record_ai_consent(
            _PATIENT_ID,
            "declined",
            date(2026, 11, 2),
            "clinician",
            _USER_ID,
            today=_LATER,
            repo=repo,
        )
        record_ai_consent(
            _PATIENT_ID,
            "consented",
            date(2026, 10, 6),
            "clinician",
            _USER_ID,
            today=_LATER,
            repo=repo,
        )

        current = ai_consent_record(_PATIENT_ID, repo=repo).current
        assert current is not None
        assert current.decision == "consented"

    def test_no_answer_is_not_asked_yet(self) -> None:
        record = ai_consent_record(_PATIENT_ID, repo=InMemoryClientAiConsentRepository())
        assert record.current is None
        assert record.history == []

    def test_future_date_is_refused(self) -> None:
        repo = InMemoryClientAiConsentRepository()
        with pytest.raises(FutureAiConsentDateError):
            record_ai_consent(
                _PATIENT_ID,
                "consented",
                date(2026, 10, 7),
                "clinician",
                _USER_ID,
                today=date(2026, 10, 6),
                repo=repo,
            )
        assert repo.events == []

    def test_today_is_allowed(self) -> None:
        repo = InMemoryClientAiConsentRepository()
        record_ai_consent(
            _PATIENT_ID,
            "consented",
            date(2026, 10, 6),
            "clinician",
            _USER_ID,
            today=date(2026, 10, 6),
            repo=repo,
        )
        assert len(repo.events) == 1

    def test_clinician_entry_needs_who_recorded_it(self) -> None:
        with pytest.raises(ValueError, match="recorded"):
            record_ai_consent(
                _PATIENT_ID,
                "consented",
                date(2026, 10, 6),
                "clinician",
                None,
                repo=InMemoryClientAiConsentRepository(),
            )

    def test_intake_form_entry_names_its_submission(self) -> None:
        repo = InMemoryClientAiConsentRepository()
        with pytest.raises(ValueError, match="submission"):
            record_ai_consent(
                _PATIENT_ID, "consented", date(2026, 10, 6), "intake_form", None, repo=repo
            )
        with pytest.raises(ValueError, match="submission"):
            record_ai_consent(
                _PATIENT_ID,
                "consented",
                date(2026, 10, 6),
                "clinician",
                _USER_ID,
                "33333333-3333-4333-8333-333333333333",
                repo=repo,
            )

        event = record_ai_consent(
            _PATIENT_ID,
            "consented",
            date(2026, 10, 6),
            "intake_form",
            None,
            "33333333-3333-4333-8333-333333333333",
            today=_LATER,
            repo=repo,
        )
        assert event.source == "intake_form"
        assert event.intake_submission_id == "33333333-3333-4333-8333-333333333333"


class TestRoute:
    def test_agreed_then_declined_over_http(self) -> None:
        repo = InMemoryClientAiConsentRepository()
        client, _ = _client(repo)

        first = client.post(_URL, json={"decision": "consented", "effective_on": "2026-10-01"})
        assert first.status_code == 201
        second = client.post(_URL, json={"decision": "declined", "effective_on": "2026-10-02"})
        assert second.status_code == 201

        body = client.get(_URL).json()
        assert body["current"]["decision"] == "declined"
        assert body["current"]["source"] == "clinician"
        assert [(e["decision"], e["effective_on"]) for e in body["history"]] == [
            ("consented", "2026-10-01"),
            ("declined", "2026-10-02"),
        ]
        assert second.json() == body
        assert len(repo.events) == 2

    def test_not_asked_yet_reads_null(self) -> None:
        client, _ = _client(InMemoryClientAiConsentRepository())
        response = client.get(_URL)
        assert response.status_code == 200
        assert response.json() == {"current": None, "history": []}

    def test_future_date_is_400(self) -> None:
        repo = InMemoryClientAiConsentRepository()
        client, _ = _client(repo)
        tomorrow = (datetime.now(UTC) + timedelta(days=1)).date().isoformat()

        response = client.post(_URL, json={"decision": "consented", "effective_on": tomorrow})

        assert response.status_code == 400
        assert repo.events == []

    def test_omitted_date_is_today_in_the_clinicians_timezone(self) -> None:
        """Whichever side of UTC midnight the test runs, an omitted date is the
        clinician's own day, not the server's."""
        tz = ZoneInfo("Pacific/Kiritimati")  # UTC+14: the first to reach tomorrow
        repo = InMemoryClientAiConsentRepository()
        client, _ = _client(repo, tz=tz)

        response = client.post(_URL, json={"decision": "consented"})

        assert response.status_code == 201
        assert repo.events[0].effective_on == datetime.now(tz).date()

    def test_today_in_a_timezone_ahead_of_utc_is_not_the_future(self) -> None:
        tz = ZoneInfo("Pacific/Kiritimati")
        client, _ = _client(InMemoryClientAiConsentRepository(), tz=tz)
        today_there = datetime.now(tz).date().isoformat()

        response = client.post(_URL, json={"decision": "consented", "effective_on": today_there})

        assert response.status_code == 201

    def test_unknown_decision_is_422(self) -> None:
        repo = InMemoryClientAiConsentRepository()
        client, _ = _client(repo)
        response = client.post(_URL, json={"decision": "maybe"})
        assert response.status_code == 422
        assert repo.events == []

    def test_each_post_writes_one_audit_row_naming_patient_and_decision(self) -> None:
        client, audit = _client(InMemoryClientAiConsentRepository())

        client.post(_URL, json={"decision": "consented", "effective_on": "2026-10-01"})
        client.post(_URL, json={"decision": "declined", "effective_on": "2026-10-02"})

        rows = _actions(audit, "patient_ai_consent_recorded")
        assert len(rows) == 2
        assert sorted(r.changes["decision"] for r in rows) == ["consented", "declined"]
        for row in rows:
            assert row.patient_id == _PATIENT_ID
            assert row.resource_id == _PATIENT_ID
            assert set(row.changes) == {"event_id", "decision", "effective_on", "source"}

    def test_reads_are_audited(self) -> None:
        client, audit = _client(InMemoryClientAiConsentRepository())

        client.get(_URL)

        rows = _actions(audit, "patient_ai_consent_viewed")
        assert len(rows) == 1
        assert rows[0].patient_id == _PATIENT_ID

    @pytest.mark.parametrize("method", ["get", "post"])
    def test_unseen_client_is_404_and_nothing_is_written(self, method: str) -> None:
        repo = InMemoryClientAiConsentRepository()
        client, audit = _client(repo, visible=False)

        response = client.request(method.upper(), _URL, json={"decision": "consented"})

        assert response.status_code == 404
        assert repo.events == []
        assert audit._repo.list_for_user(_USER_ID) == []  # type: ignore[attr-defined]  # in-memory audit repo exposes its rows for tests

    def test_unknown_client_id_is_404(self) -> None:
        client, _ = _client(InMemoryClientAiConsentRepository())
        response = client.get(f"/api/patients/{_OTHER_PATIENT_ID}/ai-consent")
        assert response.status_code == 404
