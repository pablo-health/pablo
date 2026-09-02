# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Tests for Google Calendar sync: token encryption, OAuth, appointment mapping, reminders."""

from __future__ import annotations

import base64
import json
import logging
import os
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING
from unittest.mock import MagicMock, Mock, patch

if TYPE_CHECKING:
    from collections.abc import Generator

import pytest
from app.calendar_providers.capabilities import CalendarWriteTarget
from app.calendar_providers.oauth_state import OAuthStateError, mint_state, verify_state
from app.repositories.google_calendar_token import GoogleCalendarTokenDoc
from app.scheduling_engine.models.appointment import Appointment
from app.services.google_calendar_service import GoogleCalendarService, _build_flow
from app.services.reminder_service import ReminderService
from app.services.token_encryption import (
    TokenEncryptionError,
    decrypt_tokens,
    derive_subkey,
    encrypt_tokens,
    generate_encryption_key,
)
from app.settings import get_settings

# Fixtures


@pytest.fixture(autouse=True)
def _set_encryption_key(monkeypatch: pytest.MonkeyPatch) -> Generator[None]:
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


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _state_for(user_id: str) -> str:
    """A state value as get_auth_url would have minted for this user."""
    return mint_state(derive_subkey("google-calendar-oauth-state"), user_id)


def _oauth_credentials() -> MagicMock:
    """Stand-in for the credentials a completed OAuth flow hands back."""
    credentials = MagicMock()
    credentials.token = "ya29.access"
    credentials.refresh_token = "1//refresh"
    credentials.token_uri = "https://oauth2.googleapis.com/token"
    credentials.client_id = "test-client-id"
    credentials.client_secret = "test-client-secret"
    return credentials


# Token Encryption Tests


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


# OAuth URL Generation Tests


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
        kwargs = mock_flow.authorization_url.call_args.kwargs
        assert kwargs["access_type"] == "offline"
        assert kwargs["prompt"] == "consent"
        # The state binds the request to this user and is not the user id
        # itself, which anyone could have guessed.
        assert kwargs["state"] != "user-001"
        verify_state(derive_subkey("google-calendar-oauth-state"), kwargs["state"], "user-001")

    @patch("app.services.google_calendar_service._build_flow")
    def test_each_authorization_gets_its_own_state(
        self,
        mock_build_flow: Mock,
        calendar_service: GoogleCalendarService,
    ) -> None:
        mock_build_flow.return_value.authorization_url.return_value = ("https://url", "state")

        calendar_service.get_auth_url("user-001", "http://localhost/callback")
        calendar_service.get_auth_url("user-001", "http://localhost/callback")

        calls = mock_build_flow.return_value.authorization_url.call_args_list
        first, second = (call.kwargs["state"] for call in calls)
        assert first != second

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

        calendar_service.handle_callback(
            "user-001",
            "auth-code",
            "http://localhost/callback",
            state=_state_for("user-001"),
            write_target=CalendarWriteTarget.PRIMARY,
        )

        token_repo.save.assert_called_once()
        saved_doc = token_repo.save.call_args[0][0]
        assert saved_doc.user_id == "user-001"
        assert saved_doc.calendar_id == "primary@gmail.com"
        assert saved_doc.write_target == "primary"
        assert saved_doc.encrypted_tokens != ""
        decrypted = decrypt_tokens(saved_doc.encrypted_tokens)
        assert decrypted["token"] == "ya29.access"
        assert decrypted["refresh_token"] == "1//refresh"

    @patch("app.services.google_calendar_service._build_calendar_service")
    @patch("app.services.google_calendar_service._build_flow")
    def test_handle_callback_creates_the_pablo_owned_calendar(
        self,
        mock_build_flow: Mock,
        mock_build_svc: Mock,
        calendar_service: GoogleCalendarService,
        token_repo: MagicMock,
    ) -> None:
        """The default choice binds the connection to a calendar Pablo makes."""
        mock_build_flow.return_value.credentials = _oauth_credentials()

        mock_service = MagicMock()
        mock_service.calendarList().list().execute.return_value = {"items": []}
        mock_service.calendars().insert().execute.return_value = {
            "id": "pablo-made@group.calendar.google.com"
        }
        mock_build_svc.return_value = mock_service

        calendar_service.handle_callback(
            "user-001",
            "auth-code",
            "http://localhost/callback",
            state=_state_for("user-001"),
        )

        saved_doc = token_repo.save.call_args[0][0]
        assert saved_doc.calendar_id == "pablo-made@group.calendar.google.com"
        assert saved_doc.write_target == "app_calendar"
        mock_service.calendars().get.assert_not_called()

    @patch("app.services.google_calendar_service._build_calendar_service")
    @patch("app.services.google_calendar_service._build_flow")
    def test_reconnecting_reuses_the_calendar_pablo_already_made(
        self,
        mock_build_flow: Mock,
        mock_build_svc: Mock,
        calendar_service: GoogleCalendarService,
        token_repo: MagicMock,
    ) -> None:
        """A second connect must not leave a second calendar on the account."""
        mock_build_flow.return_value.credentials = _oauth_credentials()

        mock_service = MagicMock()
        mock_service.calendarList().list().execute.return_value = {
            "items": [
                {"id": "someone-elses", "summary": "Family"},
                {"id": "already-made@group.calendar.google.com", "summary": "Pablo Sessions"},
            ]
        }
        mock_build_svc.return_value = mock_service

        calendar_service.handle_callback(
            "user-001",
            "auth-code",
            "http://localhost/callback",
            state=_state_for("user-001"),
        )

        saved_doc = token_repo.save.call_args[0][0]
        assert saved_doc.calendar_id == "already-made@group.calendar.google.com"
        mock_service.calendars().insert.assert_not_called()


class TestCallbackStateValidation:
    """The callback only exchanges a code for the user who started the flow.

    Without this, a code obtained elsewhere could be handed to a signed-in
    therapist's browser and spent under their session, attaching somebody
    else's calendar to their account.
    """

    @staticmethod
    def _reject(
        calendar_service: GoogleCalendarService,
        mock_build_flow: Mock,
        token_repo: MagicMock,
        state: str,
        reason: str,
    ) -> None:
        """Assert a state value is refused, for the stated reason, before the
        authorization code is spent."""
        mock_build_flow.return_value.credentials = _oauth_credentials()

        with pytest.raises(OAuthStateError, match=reason):
            calendar_service.handle_callback(
                "user-001",
                "auth-code",
                "http://localhost/callback",
                state=state,
            )

        mock_build_flow.return_value.fetch_token.assert_not_called()
        token_repo.save.assert_not_called()

    @patch("app.services.google_calendar_service._build_flow")
    def test_missing_state_is_refused(
        self,
        mock_build_flow: Mock,
        calendar_service: GoogleCalendarService,
        token_repo: MagicMock,
    ) -> None:
        self._reject(calendar_service, mock_build_flow, token_repo, "", "missing state")

    @patch("app.services.google_calendar_service._build_flow")
    def test_state_minted_for_another_user_is_refused(
        self,
        mock_build_flow: Mock,
        calendar_service: GoogleCalendarService,
        token_repo: MagicMock,
    ) -> None:
        self._reject(
            calendar_service,
            mock_build_flow,
            token_repo,
            _state_for("someone-else"),
            "minted for a different user",
        )

    @patch("app.services.google_calendar_service._build_flow")
    def test_tampered_state_is_refused(
        self,
        mock_build_flow: Mock,
        calendar_service: GoogleCalendarService,
        token_repo: MagicMock,
    ) -> None:
        """Re-pointing a valid state at another user breaks its signature."""
        _, _, signature = _state_for("someone-else").partition(".")
        forged_body = _b64url(
            json.dumps(
                {"u": "user-001", "n": "nonce", "t": int(datetime.now(UTC).timestamp())},
                separators=(",", ":"),
                sort_keys=True,
            ).encode()
        )
        self._reject(
            calendar_service,
            mock_build_flow,
            token_repo,
            f"{forged_body}.{signature}",
            "signature does not verify",
        )

    @patch("app.services.google_calendar_service._build_flow")
    def test_unsigned_state_is_refused(
        self,
        mock_build_flow: Mock,
        calendar_service: GoogleCalendarService,
        token_repo: MagicMock,
    ) -> None:
        """The old shape — a bare user id — no longer gets through."""
        self._reject(calendar_service, mock_build_flow, token_repo, "user-001", "malformed state")

    @patch("app.services.google_calendar_service._build_flow")
    def test_expired_state_is_refused(
        self,
        mock_build_flow: Mock,
        calendar_service: GoogleCalendarService,
        token_repo: MagicMock,
    ) -> None:
        an_hour_ago = datetime.now(UTC) - timedelta(hours=1)
        with patch("app.calendar_providers.oauth_state.utc_now", return_value=an_hour_ago):
            stale = _state_for("user-001")

        self._reject(calendar_service, mock_build_flow, token_repo, stale, "expired")


# Appointment -> Google Event Mapping Tests


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


# Sync Status Tests


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


# Push Appointment Tests


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

    @patch("app.services.google_calendar_service._build_calendar_service")
    @patch("app.services.google_calendar_service.decrypt_tokens")
    @patch("app.services.google_calendar_service._make_credentials")
    def test_push_writes_to_the_calendar_pablo_made(
        self,
        mock_make_creds: Mock,
        mock_decrypt: Mock,
        mock_build_svc: Mock,
        calendar_service: GoogleCalendarService,
        token_repo: MagicMock,
        sample_appointment: Appointment,
    ) -> None:
        """A connection bound to Pablo's own calendar writes there, not to
        the therapist's main calendar — and still with a generic title."""
        mock_decrypt.return_value = {"token": "ya29.access", "refresh_token": "1//refresh"}
        mock_make_creds.return_value = MagicMock(expired=False)

        token_repo.get.return_value = GoogleCalendarTokenDoc(
            user_id="user-001",
            encrypted_tokens="encrypted",
            calendar_id="pablo-made@group.calendar.google.com",
            write_target="app_calendar",
        )

        mock_service = MagicMock()
        mock_service.events().insert().execute.return_value = {"id": "gcal-event-123"}
        mock_build_svc.return_value = mock_service

        calendar_service.push_appointment("user-001", sample_appointment)

        kwargs = mock_service.events().insert.call_args.kwargs
        assert kwargs["calendarId"] == "pablo-made@group.calendar.google.com"
        assert kwargs["body"]["summary"] == "Therapy Session"

    def test_push_returns_none_when_not_connected(
        self,
        calendar_service: GoogleCalendarService,
        token_repo: MagicMock,
        sample_appointment: Appointment,
    ) -> None:
        token_repo.get.return_value = None
        result = calendar_service.push_appointment("user-001", sample_appointment)
        assert result is None


# Inbound Sync Tests


class _FakeRequest:
    """Stands in for a googleapiclient request; replays one page or raises."""

    def __init__(self, page: dict | Exception) -> None:
        self._page = page

    def execute(self) -> dict:
        if isinstance(self._page, Exception):
            raise self._page
        return self._page


class _FakeEvents:
    def __init__(self, pages: list[dict | Exception]) -> None:
        self._pages = pages
        self.calls: list[dict] = []

    def list(self, **kwargs: object) -> _FakeRequest:
        self.calls.append(dict(kwargs))
        return _FakeRequest(self._pages[len(self.calls) - 1])


class _FakeCalendarService:
    def __init__(self, pages: list[dict | Exception]) -> None:
        self.events_resource = _FakeEvents(pages)

    def events(self) -> _FakeEvents:
        return self.events_resource


class _FakeHttpResponse:
    def __init__(self, status: int) -> None:
        self.status = status


class _FakeHttpError(Exception):
    """Mimics googleapiclient.errors.HttpError's status surface."""

    def __init__(self, status: int) -> None:
        super().__init__(f"HTTP {status}")
        self.resp = _FakeHttpResponse(status)


def _event(event_id: str, summary: str = "Busy") -> dict:
    return {
        "id": event_id,
        "summary": summary,
        "start": {"dateTime": "2026-01-01T10:00:00Z"},
        "end": {"dateTime": "2026-01-01T11:00:00Z"},
        "status": "confirmed",
    }


@pytest.fixture
def connected_token_doc() -> GoogleCalendarTokenDoc:
    return GoogleCalendarTokenDoc(
        user_id="user-001",
        encrypted_tokens="encrypted",
        calendar_id="primary@gmail.com",
    )


class TestSyncFromGoogle:
    """Inbound sync: paging, syncToken handling, and expiry recovery."""

    @staticmethod
    def _run_sync(
        calendar_service: GoogleCalendarService,
        token_repo: MagicMock,
        token_doc: GoogleCalendarTokenDoc,
        pages: list[dict | Exception],
    ) -> tuple[list[dict], _FakeCalendarService]:
        """Drive sync_from_google against a faked Google Calendar client."""
        token_repo.get.return_value = token_doc
        fake_service = _FakeCalendarService(pages)

        creds = MagicMock()
        creds.expired = False
        with (
            patch(
                "app.services.google_calendar_service.decrypt_tokens",
                return_value={"token": "ya29.access", "refresh_token": "1//refresh"},
            ),
            patch(
                "app.services.google_calendar_service._make_credentials",
                return_value=creds,
            ),
            patch(
                "app.services.google_calendar_service._build_calendar_service",
                return_value=fake_service,
            ),
        ):
            changes = calendar_service.sync_from_google("user-001")
        return changes, fake_service

    def test_follows_next_page_token_across_pages(
        self,
        calendar_service: GoogleCalendarService,
        token_repo: MagicMock,
        connected_token_doc: GoogleCalendarTokenDoc,
    ) -> None:
        """Every page is read — stopping at the first silently drops changes."""
        pages: list[dict | Exception] = [
            {"items": [_event("e1"), _event("e2")], "nextPageToken": "page-2"},
            {"items": [_event("e3"), _event("e4")], "nextPageToken": "page-3"},
            {"items": [_event("e5")], "nextSyncToken": "sync-final"},
        ]

        changes, fake_service = self._run_sync(
            calendar_service, token_repo, connected_token_doc, pages
        )

        assert [c["google_event_id"] for c in changes] == ["e1", "e2", "e3", "e4", "e5"]
        calls = fake_service.events_resource.calls
        assert len(calls) == 3
        assert "pageToken" not in calls[0]
        assert calls[1]["pageToken"] == "page-2"
        assert calls[2]["pageToken"] == "page-3"
        assert all(call["maxResults"] == 250 for call in calls)

    def test_sync_token_comes_from_final_page(
        self,
        calendar_service: GoogleCalendarService,
        token_repo: MagicMock,
        connected_token_doc: GoogleCalendarTokenDoc,
    ) -> None:
        """Only the last page carries a usable nextSyncToken."""
        pages: list[dict | Exception] = [
            {"items": [_event("e1")], "nextPageToken": "page-2", "nextSyncToken": "stale"},
            {"items": [_event("e2")], "nextSyncToken": "sync-final"},
        ]

        self._run_sync(calendar_service, token_repo, connected_token_doc, pages)

        token_repo.update_sync_token.assert_called_once_with("user-001", "sync-final")

    def test_single_page_response_unchanged(
        self,
        calendar_service: GoogleCalendarService,
        token_repo: MagicMock,
        connected_token_doc: GoogleCalendarTokenDoc,
    ) -> None:
        """Regression guard: one page still means one request and one token write."""
        pages: list[dict | Exception] = [
            {"items": [_event("e1", "Dentist")], "nextSyncToken": "sync-1"},
        ]

        changes, fake_service = self._run_sync(
            calendar_service, token_repo, connected_token_doc, pages
        )

        assert len(fake_service.events_resource.calls) == 1
        assert changes == [
            {
                "google_event_id": "e1",
                "summary": "Dentist",
                "start": {"dateTime": "2026-01-01T10:00:00Z"},
                "end": {"dateTime": "2026-01-01T11:00:00Z"},
                "status": "confirmed",
            }
        ]
        token_repo.update_sync_token.assert_called_once_with("user-001", "sync-1")

    def test_first_sync_uses_time_window_not_sync_token(
        self,
        calendar_service: GoogleCalendarService,
        token_repo: MagicMock,
        connected_token_doc: GoogleCalendarTokenDoc,
    ) -> None:
        pages: list[dict | Exception] = [{"items": [], "nextSyncToken": "sync-1"}]

        _, fake_service = self._run_sync(calendar_service, token_repo, connected_token_doc, pages)

        first_call = fake_service.events_resource.calls[0]
        assert "syncToken" not in first_call
        assert "timeMin" in first_call

    def test_expired_sync_token_clears_and_resyncs(
        self,
        calendar_service: GoogleCalendarService,
        token_repo: MagicMock,
        connected_token_doc: GoogleCalendarTokenDoc,
    ) -> None:
        """A 410 means the stored token aged out: drop it and re-sync in the same run."""
        connected_token_doc.sync_token = "expired-token"
        pages: list[dict | Exception] = [
            _FakeHttpError(410),
            {"items": [_event("e1")], "nextSyncToken": "sync-fresh"},
        ]

        changes, fake_service = self._run_sync(
            calendar_service, token_repo, connected_token_doc, pages
        )

        calls = fake_service.events_resource.calls
        assert calls[0]["syncToken"] == "expired-token"
        assert "syncToken" not in calls[1]
        assert "timeMin" in calls[1]

        token_repo.save.assert_called_once()
        assert token_repo.save.call_args[0][0].sync_token is None

        assert [c["google_event_id"] for c in changes] == ["e1"]
        token_repo.update_sync_token.assert_called_once_with("user-001", "sync-fresh")

    def test_non_gone_error_does_not_fail_the_run(
        self,
        calendar_service: GoogleCalendarService,
        token_repo: MagicMock,
        connected_token_doc: GoogleCalendarTokenDoc,
    ) -> None:
        pages: list[dict | Exception] = [_FakeHttpError(500)]

        changes, _ = self._run_sync(calendar_service, token_repo, connected_token_doc, pages)

        assert changes == []
        token_repo.update_sync_token.assert_not_called()
        token_repo.save.assert_not_called()

    def test_no_event_content_in_logs(
        self,
        calendar_service: GoogleCalendarService,
        token_repo: MagicMock,
        connected_token_doc: GoogleCalendarTokenDoc,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """HIPAA: sync logs page and change counts, never event content."""
        connected_token_doc.sync_token = "expired-token"
        secret = "Weekly-with-A-Patient"
        pages: list[dict | Exception] = [
            _FakeHttpError(410),
            {"items": [_event("e1", secret)], "nextPageToken": "page-2"},
            {"items": [_event("e2", secret)], "nextSyncToken": "sync-fresh"},
        ]

        with caplog.at_level(logging.DEBUG, logger="app.services.google_calendar_service"):
            self._run_sync(calendar_service, token_repo, connected_token_doc, pages)

        logged = " ".join(record.getMessage() for record in caplog.records)
        assert secret not in logged
        assert "e1" not in logged
        assert "2 changes" in logged
        assert "2 page(s)" in logged


# Disconnect Tests


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


# Reminder Service Tests


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

    def test_does_not_mark_24h_reminder_while_delivery_unimplemented(
        self, appointment_repo: MagicMock
    ) -> None:
        # Delivery is not yet wired up; the pass must be a no-op so appointments
        # are not permanently marked sent before any notification reaches the patient.
        appt = self._make_appointment("appt-1", 24)
        appointment_repo.list_by_range.side_effect = [
            [appt],  # 24h window
            [],  # 1h window
        ]

        service = ReminderService(appointment_repo)
        result = service.check_and_send_reminders("user-001")

        assert result["24h_sent"] == 0
        assert result["1h_sent"] == 0
        appointment_repo.update.assert_not_called()
        assert appt.reminder_24h_sent is False

    def test_does_not_mark_1h_reminder_while_delivery_unimplemented(
        self, appointment_repo: MagicMock
    ) -> None:
        appt = self._make_appointment("appt-1", 1)
        appointment_repo.list_by_range.side_effect = [
            [],  # 24h window
            [appt],  # 1h window
        ]

        service = ReminderService(appointment_repo)
        result = service.check_and_send_reminders("user-001")

        assert result["24h_sent"] == 0
        assert result["1h_sent"] == 0
        appointment_repo.update.assert_not_called()
        assert appt.reminder_1h_sent is False

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

    def test_returns_zero_counts_while_delivery_unimplemented(
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

        assert result["24h_sent"] == 0
        assert result["1h_sent"] == 0
        appointment_repo.update.assert_not_called()


class TestBuildFlowIsReachable:
    """`_build_flow` imports google_auth_oauthlib lazily, at call time.

    Every other test in the suite patches `_build_flow`, so nothing
    exercised that import — and a lazy import of an undeclared dependency
    fails nowhere until a therapist clicks Connect, which is how it reached
    a deployment as a 500 on /api/google-calendar/authorize
    (`ModuleNotFoundError: No module named 'google_auth_oauthlib'`).

    This calls the real thing. No network: `authorization_url` builds the
    consent URL locally from the client config.
    """

    def test_builds_a_consent_url_without_the_import_failing(self) -> None:
        flow = _build_flow(
            "cid.apps.googleusercontent.com",
            "client-secret",
            "https://app.example.test/dashboard/calendar",
            ["https://www.googleapis.com/auth/calendar.events"],
        )
        auth_url, _ = flow.authorization_url(access_type="offline", prompt="consent")

        assert auth_url.startswith("https://accounts.google.com/o/oauth2/auth")
        assert "dashboard%2Fcalendar" in auth_url
