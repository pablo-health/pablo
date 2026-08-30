# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Google Calendar sync service for therapist appointment management.

HIPAA Compliance:
- OAuth tokens encrypted at rest with AES-256-GCM
- No PHI (patient names, session details) included in log messages
- Google Calendar events use generic titles by default
- Pablo is source of truth for therapy appointments
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, NamedTuple

if TYPE_CHECKING:
    from datetime import datetime

from ..repositories.google_calendar_token import (
    GoogleCalendarTokenDoc,
    GoogleCalendarTokenRepository,
)
from ..utcnow import utc_now, utc_now_iso
from .token_encryption import decrypt_tokens, encrypt_tokens

if TYPE_CHECKING:
    from google.oauth2.credentials import Credentials

    from ..scheduling_engine.models.appointment import Appointment
    from ..scheduling_engine.repositories.appointment import AppointmentRepository

logger = logging.getLogger(__name__)

SCOPES = [
    "https://www.googleapis.com/auth/calendar.events",
    "https://www.googleapis.com/auth/calendar.readonly",
]

# HIPAA: generic summary avoids leaking patient names to Google
_DEFAULT_EVENT_SUMMARY = "Therapy Session"

# Page size for events().list. Google's default is 250 but it is not
# contractual — ask for a size we've sized the page loop around.
_SYNC_PAGE_SIZE = 250

# Google answers a syncToken it no longer honours with 410 Gone.
_HTTP_GONE = 410


def _now() -> datetime:
    return utc_now()


class _EventPage(NamedTuple):
    """The result of walking every page of one events().list call."""

    changes: list[dict[str, Any]]
    next_sync_token: str | None
    page_count: int


def _is_expired_sync_token(exc: Exception) -> bool:
    """Report whether an API error is Google's expired-syncToken 410.

    Duck-typed rather than caught by class: googleapiclient is a lazy
    import here, and its HttpError exposes the status two different ways
    depending on version.
    """
    status = getattr(getattr(exc, "resp", None), "status", None)
    if status is None:
        status = getattr(exc, "status_code", None)
    return status == _HTTP_GONE


def _event_to_change(event: dict[str, Any]) -> dict[str, Any]:
    """Map a Google event to the change dict the sync scheduler consumes."""
    return {
        "google_event_id": event.get("id"),
        "summary": event.get("summary", ""),
        "start": event.get("start", {}),
        "end": event.get("end", {}),
        "status": event.get("status", ""),
    }


def _build_flow(
    client_id: str,
    client_secret: str,
    redirect_uri: str,
) -> Any:
    """Lazily import and construct a google_auth_oauthlib Flow."""
    from google_auth_oauthlib.flow import Flow  # type: ignore[import-not-found]

    return Flow.from_client_config(
        {
            "web": {
                "client_id": client_id,
                "client_secret": client_secret,
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
            }
        },
        scopes=SCOPES,
        redirect_uri=redirect_uri,
    )


def _build_calendar_service(credentials: Any) -> Any:
    """Lazily import and build a Google Calendar API service."""
    from googleapiclient.discovery import build  # type: ignore[import-untyped,import-not-found]

    return build("calendar", "v3", credentials=credentials)


def _make_credentials(
    token: str | None,
    refresh_token: str | None,
    token_uri: str,
    client_id: str,
    client_secret: str,
) -> Credentials:
    """Lazily import and construct google.oauth2 Credentials."""
    from google.oauth2.credentials import Credentials as CredentialsCls

    return CredentialsCls(  # type: ignore[no-untyped-call]
        token=token,
        refresh_token=refresh_token,
        token_uri=token_uri,
        client_id=client_id,
        client_secret=client_secret,
    )


def _refresh_credentials(credentials: Credentials) -> None:
    """Refresh expired credentials using Google auth transport."""
    from google.auth.transport.requests import Request as GoogleAuthRequest

    credentials.refresh(GoogleAuthRequest())  # type: ignore[no-untyped-call]


class GoogleCalendarError(Exception):
    """Raised when a Google Calendar operation fails."""


class GoogleCalendarService:
    """Manages Google Calendar OAuth and bidirectional sync.

    Outbound: pushes appointment create/update/delete to Google Calendar.
    Inbound: polls with syncToken for incremental changes from Google.
    """

    def __init__(
        self,
        token_repo: GoogleCalendarTokenRepository,
        appointment_repo: AppointmentRepository,
        *,
        client_id: str,
        client_secret: str,
    ) -> None:
        self._token_repo = token_repo
        self._appointment_repo = appointment_repo
        self._client_id = client_id
        self._client_secret = client_secret

    def get_auth_url(self, user_id: str, redirect_uri: str) -> str:
        """Generate Google OAuth authorization URL for calendar access."""
        flow = _build_flow(self._client_id, self._client_secret, redirect_uri)
        auth_url, _ = flow.authorization_url(
            access_type="offline",
            prompt="consent",
            state=user_id,
        )
        # HIPAA: log action without user-identifying details
        logger.info("Generated Google Calendar OAuth URL for authorization")
        return str(auth_url)

    def handle_callback(self, user_id: str, code: str, redirect_uri: str) -> None:
        """Exchange OAuth authorization code for tokens, encrypt and store."""
        flow = _build_flow(self._client_id, self._client_secret, redirect_uri)
        flow.fetch_token(code=code)
        credentials = flow.credentials

        token_data = {
            "token": credentials.token or "",
            "refresh_token": credentials.refresh_token or "",
            "token_uri": credentials.token_uri or "",
            "client_id": credentials.client_id or "",
            "client_secret": credentials.client_secret or "",
        }

        encrypted = encrypt_tokens(token_data)

        # Get primary calendar ID
        calendar_id = self._get_primary_calendar_id(credentials)

        now = _now()
        token_doc = GoogleCalendarTokenDoc(
            user_id=user_id,
            encrypted_tokens=encrypted,
            calendar_id=calendar_id,
            connected_at=now,
            last_synced_at=now,
        )
        self._token_repo.save(token_doc)
        logger.info("Google Calendar connected and tokens stored (encrypted)")

    def push_appointment(self, user_id: str, appointment: Appointment) -> str | None:
        """Create or update a Google Calendar event for an appointment.

        Returns the Google event ID, or None if the user is not connected.
        """
        credentials = self._get_credentials(user_id)
        if not credentials:
            return None

        token_doc = self._token_repo.get(user_id)
        if not token_doc or not token_doc.calendar_id:
            return None

        event_body = self._appointment_to_event(appointment)
        service = _build_calendar_service(credentials)

        if appointment.google_event_id:
            event = (
                service.events()
                .update(
                    calendarId=token_doc.calendar_id,
                    eventId=appointment.google_event_id,
                    body=event_body,
                )
                .execute()
            )
            logger.info("Updated Google Calendar event")
        else:
            event = (
                service.events().insert(calendarId=token_doc.calendar_id, body=event_body).execute()
            )
            logger.info("Created Google Calendar event")

        return event.get("id")  # type: ignore[no-any-return]

    def delete_event(self, user_id: str, google_event_id: str) -> bool:
        """Delete a Google Calendar event."""
        credentials = self._get_credentials(user_id)
        if not credentials:
            return False

        token_doc = self._token_repo.get(user_id)
        if not token_doc or not token_doc.calendar_id:
            return False

        service = _build_calendar_service(credentials)
        try:
            service.events().delete(
                calendarId=token_doc.calendar_id,
                eventId=google_event_id,
            ).execute()
            logger.info("Deleted Google Calendar event")
            return True
        except Exception:
            logger.exception("Failed to delete Google Calendar event")
            return False

    def sync_from_google(self, user_id: str) -> list[dict[str, Any]]:
        """Poll Google Calendar for incremental changes using syncToken.

        Returns a list of change dicts for the caller to process.
        Pablo is source of truth — external events are stored as informational only.
        """
        credentials = self._get_credentials(user_id)
        if not credentials:
            return []

        token_doc = self._token_repo.get(user_id)
        if not token_doc or not token_doc.calendar_id:
            return []

        service = _build_calendar_service(credentials)

        try:
            try:
                page = self._list_all_events(
                    service,
                    token_doc.calendar_id,
                    sync_token=token_doc.sync_token,
                )
            except Exception as exc:
                if not _is_expired_sync_token(exc):
                    raise
                # Google has aged out the stored token. Drop it and start over
                # from a fresh window rather than failing the scheduled run.
                logger.info("Google Calendar sync token expired; re-syncing from a fresh window")
                token_doc.sync_token = None
                self._token_repo.save(token_doc)
                page = self._list_all_events(service, token_doc.calendar_id, sync_token=None)

            if page.next_sync_token:
                self._token_repo.update_sync_token(user_id, page.next_sync_token)

            logger.info(
                "Synced %d changes from Google Calendar over %d page(s)",
                len(page.changes),
                page.page_count,
            )
        except Exception:
            # HIPAA: don't log response bodies that might contain PHI
            logger.exception("Google Calendar sync failed")
            return []

        return page.changes

    @staticmethod
    def _list_all_events(
        service: Any,
        calendar_id: str,
        *,
        sync_token: str | None,
    ) -> _EventPage:
        """Walk every page of events().list, collecting changes and the sync token.

        Google splits large result sets across pages and only returns
        nextSyncToken on the final one, so reading a single page both drops
        changes and leaves the next poll with nothing to resume from.
        """
        kwargs: dict[str, Any] = {
            "calendarId": calendar_id,
            "singleEvents": True,
            "maxResults": _SYNC_PAGE_SIZE,
        }
        if sync_token:
            kwargs["syncToken"] = sync_token
        else:
            # First sync: only get future events
            kwargs["timeMin"] = utc_now_iso()

        changes: list[dict[str, Any]] = []
        page_count = 0
        while True:
            result = service.events().list(**kwargs).execute()
            page_count += 1
            changes.extend(_event_to_change(event) for event in result.get("items", []))

            page_token = result.get("nextPageToken")
            if not page_token:
                return _EventPage(changes, result.get("nextSyncToken"), page_count)
            kwargs["pageToken"] = page_token

    def disconnect(self, user_id: str) -> bool:
        """Remove stored tokens, disconnecting Google Calendar."""
        deleted = self._token_repo.delete(user_id)
        if deleted:
            logger.info("Google Calendar disconnected")
        return deleted

    def get_sync_status(self, user_id: str) -> dict[str, Any]:
        """Check connection status and last sync time."""
        token_doc = self._token_repo.get(user_id)
        if not token_doc:
            return {
                "connected": False,
                "calendar_id": None,
                "last_synced_at": None,
            }
        return {
            "connected": True,
            "calendar_id": token_doc.calendar_id,
            "last_synced_at": token_doc.last_synced_at,
        }

    def _get_credentials(self, user_id: str) -> Credentials | None:
        """Load and refresh OAuth credentials for a user."""
        token_doc = self._token_repo.get(user_id)
        if not token_doc:
            return None

        token_data = decrypt_tokens(token_doc.encrypted_tokens)
        credentials = _make_credentials(
            token=token_data.get("token"),
            refresh_token=token_data.get("refresh_token"),
            token_uri=token_data.get("token_uri", "https://oauth2.googleapis.com/token"),
            client_id=token_data.get("client_id", self._client_id),
            client_secret=token_data.get("client_secret", self._client_secret),
        )

        if credentials.expired and credentials.refresh_token:
            _refresh_credentials(credentials)
            # Re-encrypt updated tokens
            updated_data = {
                "token": credentials.token or "",
                "refresh_token": credentials.refresh_token or "",
                "token_uri": credentials.token_uri or "",
                "client_id": credentials.client_id or "",
                "client_secret": credentials.client_secret or "",
            }
            token_doc.encrypted_tokens = encrypt_tokens(updated_data)
            self._token_repo.save(token_doc)
            logger.info("Refreshed and re-encrypted OAuth tokens")

        return credentials

    def _get_primary_calendar_id(self, credentials: Credentials) -> str:
        """Get the user's primary Google Calendar ID."""
        service = _build_calendar_service(credentials)
        calendar = service.calendars().get(calendarId="primary").execute()
        return calendar.get("id", "primary")  # type: ignore[no-any-return]

    @staticmethod
    def _appointment_to_event(appointment: Appointment) -> dict[str, Any]:
        """Map a Pablo appointment to a Google Calendar event body.

        HIPAA: Uses generic summary. Patient name is NOT sent to Google.
        """
        event: dict[str, Any] = {
            "summary": _DEFAULT_EVENT_SUMMARY,
            "start": {
                "dateTime": appointment.start_at.isoformat(),
                "timeZone": "UTC",
            },
            "end": {
                "dateTime": appointment.end_at.isoformat(),
                "timeZone": "UTC",
            },
            "description": f"Session type: {appointment.session_type}",
            "extendedProperties": {
                "private": {
                    "pablo_appointment_id": appointment.id,
                }
            },
        }
        if appointment.video_link:
            event["conferenceData"] = {
                "entryPoints": [
                    {
                        "entryPointType": "video",
                        "uri": appointment.video_link,
                    }
                ],
            }
        return event
