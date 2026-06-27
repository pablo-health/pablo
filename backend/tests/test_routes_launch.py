# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Tests for the companion launch-intent handoff endpoints.

Covers the happy path (issue + redeem), the flag-off 404, and the
anti-oracle redeem failures (unknown / expired / already-consumed /
wrong-user), all collapsed into the same generic 410.
"""

from datetime import datetime
from typing import Any

import pytest
from app.auth.service import (
    TenantContext,
    get_tenant_context,
    require_baa_acceptance,
)
from app.main import app as real_app
from app.models import User
from app.models.patient import Patient
from app.routes import launch
from app.scheduling_engine.models.appointment import Appointment
from app.services import get_audit_service
from app.services.launch_intent_store import InMemoryLaunchIntentStore
from app.utcnow import utc_now
from fastapi import FastAPI
from fastapi.testclient import TestClient


def _make_user(user_id: str = "user-1") -> User:
    return User(
        id=user_id,
        email="t@example.com",
        name="Test Therapist",
        created_at=datetime.fromisoformat("2024-01-01T00:00:00+00:00"),
        baa_accepted_at=datetime.fromisoformat("2024-01-01T00:00:00+00:00"),
        baa_version="2024-01-01",
    )


def _make_appointment(
    *,
    appt_id: str = "appt-1",
    user_id: str = "user-1",
    patient_id: str = "pt-1",
    video_link: str | None = "https://zoom.us/j/123",
    session_id: str | None = None,
) -> Appointment:
    now = utc_now()
    return Appointment(
        id=appt_id,
        user_id=user_id,
        patient_id=patient_id,
        title="Session",
        start_at=now,
        end_at=now,
        duration_minutes=50,
        status="confirmed",
        session_type="individual",
        video_link=video_link,
        session_id=session_id,
    )


def _make_patient(patient_id: str = "pt-1") -> Patient:
    now = utc_now()
    return Patient(
        id=patient_id,
        first_name="Jane",
        last_name="Roe",
        created_at=now,
        updated_at=now,
    )


class _FakeAppointmentRepo:
    def __init__(self, appointments: dict[str, Appointment]) -> None:
        self._appointments = appointments

    def get(self, appointment_id: str, user_id: str) -> Appointment | None:
        appt = self._appointments.get(appointment_id)
        if appt is None or appt.user_id != user_id:
            return None
        return appt


class _FakePatientRepo:
    def __init__(self, patients: dict[str, Patient]) -> None:
        self._patients = patients

    def get(self, patient_id: str, user_id: str) -> Patient | None:
        return self._patients.get(patient_id)


class _FakeAudit:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def log(self, action: Any, user: Any, request: Any, **kwargs: Any) -> None:
        self.calls.append({"action": action, "user": user, **kwargs})


@pytest.fixture
def store() -> InMemoryLaunchIntentStore:
    """A fresh in-memory store, patched in for the duration of a test."""
    return InMemoryLaunchIntentStore()


@pytest.fixture
def fake_audit() -> _FakeAudit:
    return _FakeAudit()


@pytest.fixture
def launch_client(
    store: InMemoryLaunchIntentStore,
    fake_audit: _FakeAudit,
    monkeypatch: pytest.MonkeyPatch,
) -> Any:
    """A TestClient over an app that mounts only the launch router.

    The launch router is flag-gated in the real app, so we mount it
    directly here and override its deps. The launch-intent store module
    is patched to the per-test in-memory store so create+redeem share
    state without a DB.
    """
    user = _make_user()
    appointment = _make_appointment()
    patient = _make_patient()

    appt_repo = _FakeAppointmentRepo({appointment.id: appointment})
    patient_repo = _FakePatientRepo({patient.id: patient})

    monkeypatch.setattr(launch, "create_launch_intent", store.create)
    monkeypatch.setattr(launch, "redeem_launch_intent", store.redeem)

    app = FastAPI()
    app.include_router(launch.router)

    app.dependency_overrides[require_baa_acceptance] = lambda: user
    app.dependency_overrides[get_tenant_context] = lambda: TenantContext(
        user_id=user.id, practice_id="p", practice_schema="practice_p"
    )
    app.dependency_overrides[launch.get_appointment_repository] = lambda: appt_repo
    app.dependency_overrides[launch.get_patient_repository] = lambda: patient_repo
    app.dependency_overrides[get_audit_service] = lambda: fake_audit

    client = TestClient(app)
    client.store = store  # type: ignore[attr-defined]
    client.fake_audit = fake_audit  # type: ignore[attr-defined]
    yield client
    app.dependency_overrides.clear()


def test_issue_intent_returns_opaque_id_and_launch_url(launch_client: TestClient) -> None:
    resp = launch_client.post("/api/launch/intent", json={"appointment_id": "appt-1"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["expires_in"] == 180
    # 128-bit token_urlsafe(16) → 22 chars, no padding.
    assert len(body["intent_id"]) == 22
    assert body["launch_url"].endswith(f"/launch/{body['intent_id']}")
    # The raw appointment id is NOT in the launch URL.
    assert "appt-1" not in body["launch_url"]


def test_issue_intent_unknown_appointment_404(launch_client: TestClient) -> None:
    resp = launch_client.post("/api/launch/intent", json={"appointment_id": "nope"})
    assert resp.status_code == 404
    assert resp.json()["detail"] == "Appointment not found."


def test_issue_intent_missing_field_422(launch_client: TestClient) -> None:
    assert launch_client.post("/api/launch/intent", json={}).status_code == 422
    assert launch_client.post("/api/launch/intent", json={"appointment_id": ""}).status_code == 422


def test_redeem_happy_path_returns_appointment_and_audits(launch_client: TestClient) -> None:
    issued = launch_client.post("/api/launch/intent", json={"appointment_id": "appt-1"}).json()
    resp = launch_client.post("/api/launch/redeem", json={"intent_id": issued["intent_id"]})
    assert resp.status_code == 200
    body = resp.json()
    assert body["appointment_id"] == "appt-1"
    assert body["patient_name"] == "Jane Roe"
    assert body["video_url"] == "https://zoom.us/j/123"
    assert body["session_id"] is None

    # One record-level audit event; no PHI in the changes payload.
    audit: _FakeAudit = launch_client.fake_audit  # type: ignore[attr-defined]
    assert len(audit.calls) == 1
    call = audit.calls[0]
    assert str(call["action"]) == "launch_intent_redeemed"
    changes = call.get("changes") or {}
    assert "patient_name" not in changes
    assert "video_url" not in changes
    assert "intent_id" not in changes
    # The patient association rides the patient= argument, not changes.
    assert call.get("patient") is not None


def test_redeem_is_single_use(launch_client: TestClient) -> None:
    issued = launch_client.post("/api/launch/intent", json={"appointment_id": "appt-1"}).json()
    first = launch_client.post("/api/launch/redeem", json={"intent_id": issued["intent_id"]})
    assert first.status_code == 200
    second = launch_client.post("/api/launch/redeem", json={"intent_id": issued["intent_id"]})
    assert second.status_code == 410
    assert second.json()["detail"] == "Launch intent is no longer valid."


def test_redeem_unknown_intent_410(launch_client: TestClient) -> None:
    resp = launch_client.post("/api/launch/redeem", json={"intent_id": "does-not-exist"})
    assert resp.status_code == 410
    assert resp.json()["detail"] == "Launch intent is no longer valid."


def test_redeem_expired_intent_410(launch_client: TestClient) -> None:
    store: InMemoryLaunchIntentStore = launch_client.store  # type: ignore[attr-defined]
    store.ttl_seconds = 0  # everything is immediately expired
    issued = launch_client.post("/api/launch/intent", json={"appointment_id": "appt-1"}).json()
    resp = launch_client.post("/api/launch/redeem", json={"intent_id": issued["intent_id"]})
    assert resp.status_code == 410


def test_redeem_wrong_user_410_and_no_audit(
    launch_client: TestClient,
) -> None:
    """A redeem by a different user burns the intent and returns the same 410."""
    # Mint an intent bound to a different user directly in the store.
    other_intent = launch_client.store.create(  # type: ignore[attr-defined]
        user_id="someone-else", appointment_id="appt-1"
    )
    resp = launch_client.post("/api/launch/redeem", json={"intent_id": other_intent})
    assert resp.status_code == 410
    assert resp.json()["detail"] == "Launch intent is no longer valid."
    # No PHI disclosure happened, so no audit event.
    assert launch_client.fake_audit.calls == []  # type: ignore[attr-defined]
    # And the intent was consumed (single-use) even on the wrong-user path.
    again = launch_client.post("/api/launch/redeem", json={"intent_id": other_intent})
    assert again.status_code == 410


def test_redeem_missing_field_422(launch_client: TestClient) -> None:
    assert launch_client.post("/api/launch/redeem", json={}).status_code == 422


def test_routes_return_404_when_flag_off() -> None:
    """With ENABLE_LAUNCH_INTENT unset (test default), the router is unmounted."""
    client = TestClient(real_app)
    assert client.post("/api/launch/intent", json={"appointment_id": "x"}).status_code == 404
    assert client.post("/api/launch/redeem", json={"intent_id": "x"}).status_code == 404
