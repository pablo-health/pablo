# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Tests for Google Calendar sync: token encryption, OAuth, appointment mapping, reminders."""

from __future__ import annotations

import base64
import os
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING
from unittest.mock import MagicMock, Mock, patch

if TYPE_CHECKING:
    from collections.abc import Generator

import pytest
from app.repositories.google_calendar_token import GoogleCalendarTokenDoc
from app.scheduling_engine.models.appointment import Appointment
from app.services.google_calendar_service import GoogleCalendarService
from app.services.reminder_service import ReminderService
from app.services.token_encryption import (
    TokenEncryptionError,
    decrypt_tokens,
    encrypt_tokens,
    generate_encryption_key,
)
from app.settings import get_settings

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _set_encryption_key(monkeypatch: pytest.MonkeyPatch) -> Generator[None, None, None]:
    """Provide a valid AES-256 encryption key for all tests."""
    key = base64.b64encode(os.urandom(32)).decode()
    monkeypatch.setenv("GOOGLE_CALENDAR_ENCRYPTION_KEY", key)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def token_repo() -> MagicMock:
    return MagicMock()


@pytest.fixture
def appointment_repo() -> MagicMock:
    return MagicMock()


@pytest.fixture
def calendar_service(
    token_repo: MagicMock,
    appointment_repo: MagicMock,
) -> GoogleCalendarService:
    return GoogleCalendarService(
        token_repo=token_repo,
        appointment_repo=appointment_repo,
        client_id="test-client-id",
        client_secret="test-client-secret",  # noqa: S106
    )


@pytest.fixture
def sample_appointment() -> Appointment:
    now = datetime.now(UTC)
    return Appointment(
        id="appt-001",
        user_id="user-001",
        patient_id="patient-001",
        title="Session with Patient",
        start_at=now,
        end_at=now + timedelta(hours=1),
        duration_minutes=60,
        status="confirmed",
        session_type="individual",
        created_at=now,
    )


# ---------------------------------------------------------------------------
# Token Encryption Tests
# ---------------------------------------------------------------------------


class TestTokenEncryption:
    """AES-256-GCM encryption round-trip and edge cases."""

    def test_encrypt_decrypt_round_trip(self) -> None:
        original = {
            "token": "ya29.access-token",
            "refresh_token": "1//refresh-token",
            "token_uri": "https://oauth2.googleapis.com/token",
            "client_id": "client-id",
            "client_secret": "client-secret",
        }
        encrypted = encrypt_tokens(original)
        decrypted = decrypt_tokens(encrypted)
        assert decrypted == original

    def test_encrypted_data_is_base64(self) -> None:
        encrypted = encrypt_tokens({"token": "test"})
        decoded = base64.b64decode(encrypted)
        assert len(decoded) > 12  # nonce (12) + ciphertext + tag (16)

    def test_different_nonces_produce_different_ciphertext(self) -> None:
        data = {"token": "same-token"}
        enc1 = encrypt_tokens(data)
        enc2 = encrypt_tokens(data)
        assert enc1 != enc2
        assert decrypt_tokens(enc1) == decrypt_tokens(enc2)

    def test_missing_key_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("GOOGLE_CALENDAR_ENCRYPTION_KEY", "")
        get_settings.cache_clear()
        try:
            with pytest.raises(TokenEncryptionError, match="not set"):
                encrypt_tokens({"token": "test"})
        finally:
            get_settings.cache_clear()

    def test_invalid_key_length_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        short_key = base64.b64encode(b"too-short").decode()
        monkeypatch.setenv("GOOGLE_CALENDAR_ENCRYPTION_KEY", short_key)
        get_settings.cache_clear()
        try:
            with pytest.raises(TokenEncryptionError, match="must be 32 bytes"):
                encrypt_tokens({"token": "test"})
        finally:
            get_settings.cache_clear()

    def test_tampered_ciphertext_raises(self) -> None:
        encrypted = encrypt_tokens({"token": "secret"})
        raw = bytearray(base64.b64decode(encrypted))
        raw[-1] ^= 0xFF
        tampered = base64.b64encode(bytes(raw)).decode()
        with pytest.raises(TokenEncryptionError, match="decryption failed"):
            decrypt_tokens(tampered)

    def test_generate_encryption_key(self) -> None:
        key_b64 = generate_encryption_key()
        key_bytes = base64.b64decode(key_b64)
        assert len(key_bytes) == 32


# ---------------------------------------------------------------------------
# OAuth URL Generation Tests
# ---------------------------------------------------------------------------


class TestOAuthFlow:
    """Google OAuth authorization URL and callback."""

    @patch("app.services.google_calendar_service._build_flow")
    def test_get_auth_url(
        self,
        mock_build_flow: Mock,
        calendar_service: GoogleCalendarService,
    ) -> None:
        mock_flow = MagicMock()
        mock_flow.authorization_url.return_value = (
            "https://accounts.google.com/o/oauth2/auth?client_id=test",
            "state",
        )
        mock_build_flow.return_value = mock_flow

        url = calendar_service.get_auth_url("user-001", "http://localhost:3000/callback")

        assert url.startswith("https://accounts.google.com/")
        mock_flow.authorization_url.assert_called_once_with(
            access_type="offline",
            prompt="consent",
            state="user-001",
        )

    @patch("app.services.google_calendar_service._build_calendar_service")
    @patch("app.services.google_calendar_service._build_flow")
    def test_handle_callback_stores_encrypted_tokens(
        self,
        mock_build_flow: Mock,
        mock_build_svc: Mock,
        calendar_service: GoogleCalendarService,
        token_repo: MagicMock,
    ) -> None:
        mock_flow = MagicMock()
        mock_creds = MagicMock()
        mock_creds.token = "ya29.access"
        mock_creds.refresh_token = "1//refresh"
        mock_creds.token_uri = "https://oauth2.googleapis.com/token"
        mock_creds.client_id = "test-client-id"
        mock_creds.client_secret = "test-client-secret"
        mock_flow.credentials = mock_creds
        mock_build_flow.return_value = mock_flow

        mock_service = MagicMock()
        mock_service.calendars().get().execute.return_value = {"id": "primary@gmail.com"}
        mock_build_svc.return_value = mock_service

        calendar_service.handle_callback("user-001", "auth-code", "http://localhost/callback")

        token_repo.save.assert_called_once()
        saved_doc = token_repo.save.call_args[0][0]
        assert saved_doc.user_id == "user-001"
        assert saved_doc.calendar_id == "primary@gmail.com"
        assert saved_doc.encrypted_tokens != ""
        decrypted = decrypt_tokens(saved_doc.encrypted_tokens)
        assert decrypted["token"] == "ya29.access"
        assert decrypted["refresh_token"] == "1//refresh"


# ---------------------------------------------------------------------------
# Appointment -> Google Event Mapping Tests
# ---------------------------------------------------------------------------


class TestAppointmentMapping:
    """Verify appointment-to-Google-event field mapping."""

    def test_appointment_to_event_basic(self, sample_appointment: Appointment) -> None:
        event = GoogleCalendarService._appointment_to_event(sample_appointment)

        assert "Patient" not in event["summary"]
        assert event["summary"] == "Therapy Session"
        assert event["start"]["dateTime"] == sample_appointment.start_at.isoformat()
        assert event["end"]["dateTime"] == sample_appointment.end_at.isoformat()
        assert event["extendedProperties"]["private"]["pablo_appointment_id"] == "appt-001"

    def test_appointment_to_event_with_video_link(
        self,
        sample_appointment: Appointment,
    ) -> None:
        sample_appointment.video_link = "https://zoom.us/j/123"
        event = GoogleCalendarService._appointment_to_event(sample_appointment)

        assert "conferenceData" in event
        assert event["conferenceData"]["entryPoints"][0]["uri"] == "https://zoom.us/j/123"

    def test_appointment_to_event_no_phi_in_summary(
        self,
        sample_appointment: Appointment,
    ) -> None:
        """HIPAA: patient name must never appear in Google Calendar event summary."""
        sample_appointment.title = "Session with John Smith — Anxiety"
        event = GoogleCalendarService._appointment_to_event(sample_appointment)
        assert "John" not in event["summary"]
        assert "Smith" not in event["summary"]
        assert "Anxiety" not in event["summary"]


# ---------------------------------------------------------------------------
# Sync Status Tests
# ---------------------------------------------------------------------------


class TestSyncStatus:
    """Google Calendar connection status checks."""

    def test_not_connected(
        self,
        calendar_service: GoogleCalendarService,
        token_repo: MagicMock,
    ) -> None:
        token_repo.get.return_value = None
        result = calendar_service.get_sync_status("user-001")
        assert result["connected"] is False
        assert result["calendar_id"] is None

    def test_connected(
        self,
        calendar_service: GoogleCalendarService,
        token_repo: MagicMock,
    ) -> None:
        token_repo.get.return_value = GoogleCalendarTokenDoc(
            user_id="user-001",
            encrypted_tokens="encrypted-data",
            calendar_id="user@gmail.com",
            last_synced_at=datetime.fromisoformat("2026-01-01T00:00:00+00:00"),
            connected_at=datetime.fromisoformat("2026-01-01T00:00:00+00:00"),
        )
        result = calendar_service.get_sync_status("user-001")
        assert result["connected"] is True
        assert result["calendar_id"] == "user@gmail.com"
        assert result["last_synced_at"] == datetime.fromisoformat("2026-01-01T00:00:00+00:00")


# ---------------------------------------------------------------------------
# Push Appointment Tests
# ---------------------------------------------------------------------------


class TestPushAppointment:
    """Outbound sync: pushing appointments to Google Calendar."""

    @patch("app.services.google_calendar_service._build_calendar_service")
    @patch("app.services.google_calendar_service.decrypt_tokens")
    @patch("app.services.google_calendar_service._make_credentials")
    def test_push_creates_event(
        self,
        mock_make_creds: Mock,
        mock_decrypt: Mock,
        mock_build_svc: Mock,
        calendar_service: GoogleCalendarService,
        token_repo: MagicMock,
        sample_appointment: Appointment,
    ) -> None:
        mock_decrypt.return_value = {
            "token": "ya29.access",
            "refresh_token": "1//refresh",
            "token_uri": "https://oauth2.googleapis.com/token",
            "client_id": "test-client-id",
            "client_secret": "test-client-secret",
        }
        mock_creds = MagicMock()
        mock_creds.expired = False
        mock_make_creds.return_value = mock_creds

        token_repo.get.return_value = GoogleCalendarTokenDoc(
            user_id="user-001",
            encrypted_tokens="encrypted",
            calendar_id="primary@gmail.com",
        )

        mock_service = MagicMock()
        mock_service.events().insert().execute.return_value = {"id": "gcal-event-123"}
        mock_build_svc.return_value = mock_service

        event_id = calendar_service.push_appointment("user-001", sample_appointment)
        assert event_id == "gcal-event-123"

    def test_push_returns_none_when_not_connected(
        self,
        calendar_service: GoogleCalendarService,
        token_repo: MagicMock,
        sample_appointment: Appointment,
    ) -> None:
        token_repo.get.return_value = None
        result = calendar_service.push_appointment("user-001", sample_appointment)
        assert result is None


# ---------------------------------------------------------------------------
# Disconnect Tests
# ---------------------------------------------------------------------------


class TestDisconnect:
    """Token removal on disconnect."""

    def test_disconnect_deletes_tokens(
        self,
        calendar_service: GoogleCalendarService,
        token_repo: MagicMock,
    ) -> None:
        token_repo.delete.return_value = True
        assert calendar_service.disconnect("user-001") is True
        token_repo.delete.assert_called_once_with("user-001")

    def test_disconnect_not_connected(
        self,
        calendar_service: GoogleCalendarService,
        token_repo: MagicMock,
    ) -> None:
        token_repo.delete.return_value = False
        assert calendar_service.disconnect("user-001") is False


# ---------------------------------------------------------------------------
# Reminder Service Tests
# ---------------------------------------------------------------------------


class TestReminderService:
    """Background reminder logic."""

    def _make_appointment(
        self,
        appt_id: str,
        hours_from_now: float,
        *,
        reminder_24h_sent: bool = False,
        reminder_1h_sent: bool = False,
        appt_status: str = "confirmed",
    ) -> Appointment:
        now = datetime.now(UTC)
        start = now + timedelta(hours=hours_from_now)
        end = start + timedelta(hours=1)
        return Appointment(
            id=appt_id,
            user_id="user-001",
            patient_id="patient-001",
            title="Session",
            start_at=start,
            end_at=end,
            duration_minutes=60,
            status=appt_status,
            session_type="individual",
            reminder_24h_sent=reminder_24h_sent,
            reminder_1h_sent=reminder_1h_sent,
            created_at=now,
        )

    def test_sends_24h_reminder(self, appointment_repo: MagicMock) -> None:
        appt = self._make_appointment("appt-1", 24)
        appointment_repo.list_by_range.side_effect = [
            [appt],  # 24h window
            [],  # 1h window
        ]

        service = ReminderService(appointment_repo)
        result = service.check_and_send_reminders("user-001")

        assert result["24h_sent"] == 1
        assert result["1h_sent"] == 0
        appointment_repo.update.assert_called_once()
        assert appt.reminder_24h_sent is True

    def test_sends_1h_reminder(self, appointment_repo: MagicMock) -> None:
        appt = self._make_appointment("appt-1", 1)
        appointment_repo.list_by_range.side_effect = [
            [],  # 24h window
            [appt],  # 1h window
        ]

        service = ReminderService(appointment_repo)
        result = service.check_and_send_reminders("user-001")

        assert result["24h_sent"] == 0
        assert result["1h_sent"] == 1
        assert appt.reminder_1h_sent is True

    def test_skips_already_sent_reminders(self, appointment_repo: MagicMock) -> None:
        appt = self._make_appointment("appt-1", 24, reminder_24h_sent=True)
        appointment_repo.list_by_range.side_effect = [
            [appt],  # 24h window
            [],  # 1h window
        ]

        service = ReminderService(appointment_repo)
        result = service.check_and_send_reminders("user-001")

        assert result["24h_sent"] == 0
        appointment_repo.update.assert_not_called()

    def test_skips_cancelled_appointments(self, appointment_repo: MagicMock) -> None:
        appt = self._make_appointment("appt-1", 24, appt_status="cancelled")
        appointment_repo.list_by_range.side_effect = [
            [appt],  # 24h window
            [],  # 1h window
        ]

        service = ReminderService(appointment_repo)
        result = service.check_and_send_reminders("user-001")

        assert result["24h_sent"] == 0
        appointment_repo.update.assert_not_called()

    def test_sends_both_reminders_for_different_appointments(
        self,
        appointment_repo: MagicMock,
    ) -> None:
        appt_24h = self._make_appointment("appt-24", 24)
        appt_1h = self._make_appointment("appt-1", 1)
        appointment_repo.list_by_range.side_effect = [
            [appt_24h],  # 24h window
            [appt_1h],  # 1h window
        ]

        service = ReminderService(appointment_repo)
        result = service.check_and_send_reminders("user-001")

        assert result["24h_sent"] == 1
        assert result["1h_sent"] == 1
