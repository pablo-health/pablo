# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Google Calendar — the first implementation of the calendar provider seam.

Everything Google-shaped lives behind this module: the OAuth scope strings,
the discovery-built Calendar v3 client, the ``primary`` calendar alias, the
event JSON, and the syncToken/pageToken incremental read. Callers speak
capabilities (see ``app.calendar_providers``).

HIPAA Compliance:
- OAuth tokens encrypted at rest with AES-256-GCM
- No PHI (patient names, session details) included in log messages
- Google Calendar events use generic titles by default
- Pablo is source of truth for therapy appointments
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, ClassVar, NamedTuple

from ..calendar_providers.capabilities import (
    CalendarCapability,
    CalendarWriteTarget,
    NarrowingEnforcement,
    ProviderCapability,
    UnsupportedCapabilityError,
    scopes_for,
)
from ..calendar_providers.event_titles import (
    DEFAULT_EVENT_SUMMARY,
    EventTitleStyle,
    initials_by_patient,
    parse_style,
    summary_for,
)
from ..calendar_providers.oauth_state import mint_state, verify_state
from ..calendar_providers.practice_import import (
    DEFAULT_HORIZON_DAYS,
    DEFAULT_LOOKBACK_DAYS,
    build_proposal,
)
from ..calendar_providers.provider import BusyWindow, ConsentSurface, ImportCandidate
from ..calendar_providers.registry import ProviderRegistration
from ..reliability import HTTP_REQUEST, Idempotency, call_with_retry
from ..repositories.google_calendar_token import (
    GoogleCalendarTokenDoc,
    GoogleCalendarTokenRepository,
)
from ..utcnow import utc_now, utc_now_iso
from .token_encryption import decrypt_tokens, derive_subkey, encrypt_tokens

if TYPE_CHECKING:
    from collections.abc import Callable, Collection, Mapping, Sequence

    from google.oauth2.credentials import Credentials

    from ..calendar_providers.practice_import import ImportProposal
    from ..models.patient import Patient
    from ..repositories.patient import PatientRepository
    from ..scheduling_engine.models.appointment import Appointment
    from ..scheduling_engine.repositories.appointment import AppointmentRepository
    from ..settings import Settings

logger = logging.getLogger(__name__)

GOOGLE_PROVIDER_ID = "google"

# Writing to a calendar Google let Pablo create is reachable with a grant
# that cannot touch anything else on the account, so the narrowing is
# Google's to enforce and the wizard may say so.
_PUSH_TO_APP_CALENDAR = ProviderCapability(
    capability=CalendarCapability.PUSH,
    scopes=("https://www.googleapis.com/auth/calendar.app.created",),
    incremental=False,
    enforcement=NarrowingEnforcement.PROVIDER_ENFORCED,
    reach="the calendar Pablo creates for your sessions",
)

# Writing to the therapist's own calendar has no such grant: the narrowest
# scope that can do it reaches every event on the calendar, so the limit is
# Pablo's discipline and the copy has to say that instead.
_PUSH_TO_PRIMARY = ProviderCapability(
    capability=CalendarCapability.PUSH,
    scopes=("https://www.googleapis.com/auth/calendar.events",),
    incremental=False,
    enforcement=NarrowingEnforcement.PABLO_ENFORCED,
    reach="the sessions you book in Pablo",
)

_PUSH_BY_TARGET: Mapping[CalendarWriteTarget, ProviderCapability] = MappingProxyType(
    {
        CalendarWriteTarget.APP_CALENDAR: _PUSH_TO_APP_CALENDAR,
        CalendarWriteTarget.PRIMARY: _PUSH_TO_PRIMARY,
    }
)

_BUSY = ProviderCapability(
    capability=CalendarCapability.BUSY,
    scopes=("https://www.googleapis.com/auth/calendar.freebusy",),
    incremental=False,
    enforcement=NarrowingEnforcement.PROVIDER_ENFORCED,
    reach="when you are busy — start and end times, never titles or guests",
)

_IMPORT = ProviderCapability(
    capability=CalendarCapability.IMPORT,
    scopes=("https://www.googleapis.com/auth/calendar.readonly",),
    incremental=True,
    enforcement=NarrowingEnforcement.PABLO_ENFORCED,
    reach="reading the window you pick, once, to propose your existing practice",
)

DEFAULT_WRITE_TARGET = CalendarWriteTarget.APP_CALENDAR


def google_capabilities(
    write_target: CalendarWriteTarget = DEFAULT_WRITE_TARGET,
) -> Mapping[CalendarCapability, ProviderCapability]:
    """How Google satisfies each capability, for a given write target.

    Data, not branching: a second provider is another set of declarations,
    not another code path here.
    """
    return MappingProxyType(
        {
            CalendarCapability.PUSH: _PUSH_BY_TARGET[write_target],
            CalendarCapability.BUSY: _BUSY,
            CalendarCapability.IMPORT: _IMPORT,
        }
    )


GOOGLE_CAPABILITIES = google_capabilities()

# IMPORT is declared incremental and is deliberately absent: reading event
# content is asked for when an import is run, so a therapist who never
# imports never grants it.
_CONNECT_CAPABILITIES = frozenset({CalendarCapability.PUSH, CalendarCapability.BUSY})

# Summary of the calendar Pablo creates under the app-calendar choice. It
# is how the calendar is found again on a later connect, so changing it
# strands the one already on the account.
_APP_CALENDAR_SUMMARY = "Pablo Sessions"

# Names the key that signs the OAuth state, keeping it distinct from the
# key that encrypts stored tokens.
_STATE_PURPOSE = "google-calendar-oauth-state"

# Ceilings on one import scan. A calendar big enough to reach either of
# these gets a proposal that says so — truncating quietly would leave a
# therapist believing a client simply wasn't there.
_MAX_SCAN_EVENTS = 2500
_MAX_SCAN_SERIES = 200

# The floor, and the fallback whenever a chosen style can't be rendered.
_DEFAULT_EVENT_SUMMARY = DEFAULT_EVENT_SUMMARY

# How far ahead a retitle looks. Past the horizon any recurring series
# reaches, so "future events" means all of them, while still bounding the
# work rather than walking a calendar with no end.
_RETITLE_HORIZON_DAYS = 730
_RETITLE_MAX_EVENTS = 2000

# One page big enough to hold a solo practice's whole caseload.
_CASELOAD_PAGE_SIZE = 500

# What a new connection reads as unless the therapist says otherwise.
# Initials rather than the floor: a column of identical blocks is the
# problem the choice exists to solve, and initials disclose nothing to
# someone who does not already know the caseload.
DEFAULT_EVENT_TITLING = EventTitleStyle.INITIALS

# Where full names land when the attestation behind them no longer covers
# the connected account. Initials rather than the floor: the attestation
# permitted names, so losing it takes back the names and nothing else.
UNATTESTED_FALLBACK_TITLING = EventTitleStyle.INITIALS

# Page size for events().list. Google's default is 250 but it is not
# contractual — ask for a size we've sized the page loop around.
_SYNC_PAGE_SIZE = 250

# Google answers a syncToken it no longer honours with 410 Gone.
_HTTP_GONE = 410
_HTTP_FORBIDDEN = 403
_HTTP_TOO_MANY_REQUESTS = 429


def _now() -> datetime:
    return utc_now()


class RetitleOutcome(NamedTuple):
    """What a retitle pass managed to do."""

    retitled: int
    failed: int
    skipped: int
    """Events past the per-pass ceiling, left for a further pass."""


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
    scopes: Sequence[str],
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
        scopes=list(scopes),
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


class CalendarImportNotAuthorizedError(Exception):
    """Reading event content was never granted for this connection.

    Not a failure: importing asks for that grant when an import is run, so
    the first scan on a new connection is expected to land here and be
    answered with a consent prompt.
    """

    def __init__(self, provider_id: str) -> None:
        super().__init__(f"{provider_id} connection has not granted event content access")
        self.provider_id = provider_id


class CalendarBusyNotAuthorizedError(Exception):
    """Free/busy was never granted for this connection.

    Not a failure: BUSY is an opt-in choice at connect ("Also check when
    I'm busy"), never asked for incrementally, so a connection that
    declined it — or predates the choice — is expected to land here. The
    caller falls back to whatever it can build without this endpoint.
    """

    def __init__(self, provider_id: str) -> None:
        super().__init__(f"{provider_id} connection has not granted busy/free access")
        self.provider_id = provider_id


def _split_capabilities(granted: str) -> frozenset[str]:
    return frozenset(part.strip() for part in granted.split(",") if part.strip())


def _is_rate_limited(exc: BaseException) -> bool:
    """Whether Google is asking us to slow down rather than refusing us.

    A 403 means either quota or permission depending on its reason, so the
    reason is what decides: retrying an insufficient-permission 403 would
    just spend the budget on an answer that will not change.
    """
    status = getattr(getattr(exc, "resp", None), "status", None)
    if status is None:
        status = getattr(exc, "status_code", None)
    if status == _HTTP_TOO_MANY_REQUESTS:
        return True
    if status != _HTTP_FORBIDDEN:
        return False
    return any(reason in str(exc) for reason in ("rateLimitExceeded", "userRateLimitExceeded"))


def _with_calendar_retry[T](call: Callable[[], T]) -> T:
    """Run a read against Google, backing off when it asks us to.

    Reads are side-effect free, so a retry can only cost time.
    """
    return call_with_retry(
        call,
        policy=HTTP_REQUEST,
        idempotency=Idempotency.SAFE,
        retryable=_is_rate_limited,
    )


def _patch_event_summary(service: Any, calendar_id: str, event_id: str, summary: str) -> None:
    """Change one event's title, leaving everything else about it alone."""
    _with_calendar_retry(
        lambda: (
            service.events()
            .patch(calendarId=calendar_id, eventId=event_id, body={"summary": summary})
            .execute()
        )
    )


def _read_event(service: Any, calendar_id: str, event_id: str) -> dict[str, Any]:
    """Read one event by id, backing off if Google asks us to."""
    page: dict[str, Any] = _with_calendar_retry(
        lambda: service.events().get(calendarId=calendar_id, eventId=event_id).execute()
    )
    return page


def _event_to_candidate(event: dict[str, Any]) -> ImportCandidate | None:
    """Map one expanded occurrence, skipping anything without real times.

    All-day events carry a date rather than a dateTime and are not
    sessions, so they drop out here.
    """
    start = _parse_event_time(event.get("start", {}))
    end = _parse_event_time(event.get("end", {}))
    event_id = event.get("id")
    if start is None or end is None or not event_id:
        return None
    attendees = event.get("attendees") or []
    return ImportCandidate(
        provider_event_id=str(event_id),
        start=start,
        end=end,
        summary=str(event.get("summary", "")),
        # The therapist's own invitation slot is not another person on it.
        attendee_count=sum(1 for attendee in attendees if not attendee.get("self")),
        series_id=event.get("recurringEventId"),
    )


def _parse_event_time(slot: dict[str, Any]) -> datetime | None:
    raw = slot.get("dateTime")
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


class GoogleCalendarService:
    """Google Calendar as a CalendarProvider.

    Outbound: pushes appointment create/update/delete to Google Calendar.
    Inbound: polls with syncToken for incremental changes from Google.
    """

    provider_id: ClassVar[str] = GOOGLE_PROVIDER_ID
    display_name: ClassVar[str] = "Google Calendar"

    def __init__(
        self,
        token_repo: GoogleCalendarTokenRepository,
        appointment_repo: AppointmentRepository,
        *,
        client_id: str,
        client_secret: str,
        patient_repo: PatientRepository | None = None,
    ) -> None:
        self._token_repo = token_repo
        self._appointment_repo = appointment_repo
        # Only the naming styles above the floor need it. Without one, a
        # session falls back to the generic wording rather than failing —
        # the floor is always renderable.
        self._patient_repo = patient_repo
        self._surface = ConsentSurface(
            provider_id=GOOGLE_PROVIDER_ID,
            client_id=client_id,
            client_secret=client_secret,
        )

    @classmethod
    def from_surface(
        cls,
        surface: ConsentSurface,
        *,
        token_repo: GoogleCalendarTokenRepository,
        appointment_repo: AppointmentRepository,
        patient_repo: PatientRepository | None = None,
    ) -> GoogleCalendarService:
        """Build from a configured consent surface rather than loose credentials."""
        service = cls(
            token_repo,
            appointment_repo,
            client_id=surface.client_id,
            client_secret=surface.client_secret,
            patient_repo=patient_repo,
        )
        service._surface = surface
        return service

    def capability_declarations(
        self,
        *,
        write_target: CalendarWriteTarget = DEFAULT_WRITE_TARGET,
    ) -> Mapping[CalendarCapability, ProviderCapability]:
        return google_capabilities(write_target)

    def _resolve_request(
        self,
        capabilities: Collection[CalendarCapability] | None,
    ) -> frozenset[CalendarCapability]:
        """Settle what is being asked for, or refuse it.

        A surface may be configured to offer fewer capabilities than Google
        can satisfy; asking for one it doesn't allow is an error here rather
        than a scope quietly requested anyway.
        """
        requested = frozenset(capabilities) if capabilities is not None else _CONNECT_CAPABILITIES
        not_allowed = requested - self._surface.allowed_capabilities
        if not_allowed:
            names = ", ".join(sorted(capability.value for capability in not_allowed))
            raise UnsupportedCapabilityError(f"consent surface does not allow: {names}")
        return requested

    def _scopes_for_request(
        self,
        capabilities: Collection[CalendarCapability] | None,
        write_target: CalendarWriteTarget,
    ) -> tuple[str, ...]:
        """Resolve a capability request to Google scopes, or refuse it."""
        return scopes_for(google_capabilities(write_target), self._resolve_request(capabilities))

    def get_auth_url(
        self,
        user_id: str,
        redirect_uri: str,
        *,
        capabilities: Collection[CalendarCapability] | None = None,
        write_target: CalendarWriteTarget = DEFAULT_WRITE_TARGET,
    ) -> str:
        """Generate Google OAuth authorization URL for the requested capabilities."""
        requested = self._resolve_request(capabilities)
        declarations = google_capabilities(write_target)
        flow = _build_flow(
            self._surface.client_id,
            self._surface.client_secret,
            redirect_uri,
            scopes_for(declarations, requested),
        )
        # A request made entirely of capabilities the provider declares
        # incremental is one asked for later, alongside grants already held —
        # so it has to add to them rather than replace them. A connect-time
        # request must NOT: there, the selection is the whole answer, and
        # carrying old grants forward would stop a therapist narrowing one.
        incremental = bool(requested) and all(
            declarations[capability].incremental for capability in requested
        )
        auth_url, _ = flow.authorization_url(
            access_type="offline",
            prompt="consent",
            state=mint_state(derive_subkey(_STATE_PURPOSE), user_id),
            include_granted_scopes="true" if incremental else "false",
        )
        # HIPAA: log action without user-identifying details
        logger.info("Generated Google Calendar OAuth URL for authorization")
        return str(auth_url)

    def handle_callback(
        self,
        user_id: str,
        code: str,
        redirect_uri: str,
        *,
        state: str,
        capabilities: Collection[CalendarCapability] | None = None,
        write_target: CalendarWriteTarget = DEFAULT_WRITE_TARGET,
        event_titling: EventTitleStyle = DEFAULT_EVENT_TITLING,
    ) -> None:
        """Exchange OAuth authorization code for tokens, encrypt and store.

        The state minted at authorization is checked first, so a code is
        only ever exchanged for the user the authorization was started by.
        """
        verify_state(derive_subkey(_STATE_PURPOSE), state, user_id)
        requested = self._resolve_request(capabilities)
        declarations = google_capabilities(write_target)
        scopes = scopes_for(declarations, requested)
        flow = _build_flow(
            self._surface.client_id,
            self._surface.client_secret,
            redirect_uri,
            scopes,
        )
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

        calendar_id = self._resolve_calendar_id(credentials, write_target)
        granted = self._granted_after(user_id, requested, declarations)

        now = _now()
        token_doc = GoogleCalendarTokenDoc(
            user_id=user_id,
            encrypted_tokens=encrypted,
            calendar_id=calendar_id,
            connected_at=now,
            last_synced_at=now,
            provider=GOOGLE_PROVIDER_ID,
            write_target=write_target.value,
            event_titling=event_titling.value,
            granted_capabilities=granted,
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

        event_body = self._appointment_to_event(
            appointment,
            self._summary_for(user_id, appointment, self._effective_style(token_doc)),
        )
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

    def delete_event(self, user_id: str, event_id: str) -> bool:
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
                eventId=event_id,
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
        """Check connection status, last sync time, and what was granted."""
        token_doc = self._token_repo.get(user_id)
        if not token_doc:
            return {
                "connected": False,
                "calendar_id": None,
                "last_synced_at": None,
                "write_target": None,
                "event_titling": None,
                "titling_needs_attestation": False,
            }
        return {
            "connected": True,
            "calendar_id": token_doc.calendar_id,
            "last_synced_at": token_doc.last_synced_at,
            "write_target": token_doc.write_target,
            "event_titling": self._effective_style(token_doc).value,
            "titling_needs_attestation": self._needs_reattestation(token_doc),
        }

    def list_busy_windows(
        self,
        user_id: str,
        start: datetime,
        end: datetime,
    ) -> list[BusyWindow]:
        """Free/busy windows over a window, from the therapist's own calendar.

        Reads against "primary" regardless of where PUSH writes sessions —
        busy time describes the whole account, not just a calendar Pablo
        may have made for its own events. freebusy.query answers in blocks
        of start/end only; there is no title or attendee field to leak.

        Raises CalendarBusyNotAuthorizedError when BUSY was never granted —
        it is opt-in at connect and not requested again later.
        """
        self._require_busy_grant(user_id)

        credentials = self._get_credentials(user_id)
        if not credentials:
            return []

        service = _build_calendar_service(credentials)
        body = {
            "timeMin": start.isoformat(),
            "timeMax": end.isoformat(),
            "items": [{"id": "primary"}],
        }
        result: dict[str, Any] = _with_calendar_retry(
            lambda: service.freebusy().query(body=body).execute()
        )
        busy = result.get("calendars", {}).get("primary", {}).get("busy", [])
        windows: list[BusyWindow] = []
        for block in busy:
            block_start = block.get("start")
            block_end = block.get("end")
            if not block_start or not block_end:
                continue
            windows.append(
                BusyWindow(
                    start=datetime.fromisoformat(block_start),
                    end=datetime.fromisoformat(block_end),
                )
            )
        return windows

    def _require_busy_grant(self, user_id: str) -> None:
        """Refuse to read free/busy without the grant that permits it."""
        token_doc = self._token_repo.get(user_id)
        granted = token_doc.granted_capabilities if token_doc else ""
        if CalendarCapability.BUSY.value not in _split_capabilities(granted):
            raise CalendarBusyNotAuthorizedError(self.provider_id)

    def scan_importable_events(
        self,
        user_id: str,
        start: datetime,
        end: datetime,
    ) -> list[ImportCandidate]:
        """Occurrences over a window a therapist could import as appointments.

        Asks for expanded instances rather than recurring masters. The
        window is documented against an event's own start and end, and a
        long-running series carries the start and end of its FIRST
        occurrence — so a lookback bound would drop exactly the established
        clients this is for. Instances have real times, so the window means
        what it says; the series' own rule is fetched separately.
        """
        occurrences, _ = self._scan_occurrences(user_id, start, end)
        return occurrences

    def _scan_occurrences(
        self,
        user_id: str,
        start: datetime,
        end: datetime,
    ) -> tuple[list[ImportCandidate], bool]:
        """Read every occurrence in the window. Returns (occurrences, truncated)."""
        credentials = self._get_credentials(user_id)
        token_doc = self._token_repo.get(user_id)
        if not credentials or not token_doc or not token_doc.calendar_id:
            return [], False

        service = _build_calendar_service(credentials)
        kwargs: dict[str, Any] = {
            "calendarId": token_doc.calendar_id,
            "singleEvents": True,
            "showDeleted": False,
            "orderBy": "startTime",
            "maxResults": _SYNC_PAGE_SIZE,
            "timeMin": start.isoformat(),
            "timeMax": end.isoformat(),
        }

        occurrences: list[ImportCandidate] = []
        pages = 0
        truncated = False
        while True:
            page: dict[str, Any] = _with_calendar_retry(
                lambda: service.events().list(**kwargs).execute()
            )
            pages += 1
            for event in page.get("items", []):
                if len(occurrences) >= _MAX_SCAN_EVENTS:
                    truncated = True
                    break
                candidate = _event_to_candidate(event)
                if candidate is not None:
                    occurrences.append(candidate)

            page_token = page.get("nextPageToken")
            if truncated or not page_token:
                break
            kwargs["pageToken"] = page_token

        # HIPAA: counts and pages only. What the events say never gets here.
        logger.info(
            "Read %d calendar occurrences over %d page(s) for an import scan",
            len(occurrences),
            pages,
        )
        return occurrences, truncated

    def _series_recurrence(
        self,
        user_id: str,
        series_ids: Collection[str],
    ) -> dict[str, list[str]]:
        """Fetch each series' own recurrence rule.

        The rule is both better fidelity than one inferred from spacing and
        a better staleness signal: a rule that has already run out is the
        therapist's own statement that the series finished.
        """
        credentials = self._get_credentials(user_id)
        token_doc = self._token_repo.get(user_id)
        if not credentials or not token_doc or not token_doc.calendar_id:
            return {}

        service = _build_calendar_service(credentials)
        rules: dict[str, list[str]] = {}
        for series_id in series_ids:
            try:
                master = _read_event(service, token_doc.calendar_id, series_id)
            except Exception:
                # A series whose master can't be read still gets proposed,
                # with a rule built from its observed cadence.
                logger.warning("Could not read a recurrence rule during an import scan")
                continue
            recurrence = master.get("recurrence")
            if recurrence:
                rules[series_id] = list(recurrence)
        return rules

    def scan_for_practice_import(
        self,
        user_id: str,
        *,
        lookback_days: int = DEFAULT_LOOKBACK_DAYS,
        horizon_days: int = DEFAULT_HORIZON_DAYS,
        timezone: str = "UTC",
    ) -> ImportProposal:
        """Read the calendar once and propose the practice it describes.

        The window looks two ways for two reasons: back far enough for a
        pattern to be visible, forward because only occurrences ahead of now
        are records worth creating.

        Raises CalendarImportNotAuthorizedError when reading event content was
        never granted — the caller turns that into a consent prompt.
        """
        self._require_import_grant(user_id)

        now = _now()
        start = now - timedelta(days=lookback_days)
        end = now + timedelta(days=horizon_days)

        occurrences, truncated = self._scan_occurrences(user_id, start, end)
        series_ids = {c.series_id for c in occurrences if c.series_id}
        recurrence = self._series_recurrence(user_id, series_ids) if series_ids else {}

        return build_proposal(
            occurrences,
            now=now,
            timezone=timezone,
            series_recurrence=recurrence,
            lookback_days=lookback_days,
            horizon_days=horizon_days,
            max_series=_MAX_SCAN_SERIES,
            events_read=len(occurrences),
            truncated=truncated,
        )

    def _granted_after(
        self,
        user_id: str,
        requested: Collection[CalendarCapability],
        declarations: Mapping[CalendarCapability, ProviderCapability],
    ) -> str:
        """What the connection holds once this grant lands.

        An incremental grant adds to what was already held — Google keeps
        the earlier scopes, so the record has to as well. A connect-time
        grant replaces it, because the selection made there is the whole
        answer.
        """
        names = {capability.value for capability in requested}
        incremental = bool(requested) and all(
            declarations[capability].incremental for capability in requested
        )
        if incremental:
            existing = self._token_repo.get(user_id)
            if existing:
                names |= _split_capabilities(existing.granted_capabilities)
        return ",".join(sorted(names))

    def _require_import_grant(self, user_id: str) -> None:
        """Refuse to scan without the grant that permits reading events."""
        token_doc = self._token_repo.get(user_id)
        granted = token_doc.granted_capabilities if token_doc else ""
        if CalendarCapability.IMPORT.value not in _split_capabilities(granted):
            raise CalendarImportNotAuthorizedError(self.provider_id)

    @staticmethod
    def _effective_style(token_doc: GoogleCalendarTokenDoc) -> EventTitleStyle:
        """The style actually honoured, which is not always the one stored.

        Full names rest on the therapist having confirmed that this
        calendar account is covered. That confirmation is about one
        account, so a connection now pointing at a different one is not
        covered by it and must not keep writing names — it drops to
        initials, which needs no attestation, rather than to the floor: the
        confirmation permitted names, so withdrawing it withdraws exactly
        the names.
        """
        style = parse_style(token_doc.event_titling)
        if style is not EventTitleStyle.FULL:
            return style
        attested = token_doc.titling_attested_account
        if attested and attested == (token_doc.calendar_id or ""):
            return style
        return UNATTESTED_FALLBACK_TITLING

    @staticmethod
    def _needs_reattestation(token_doc: GoogleCalendarTokenDoc) -> bool:
        """Whether a stored preference is being held back for want of one."""
        return (
            parse_style(token_doc.event_titling) is EventTitleStyle.FULL
            and GoogleCalendarService._effective_style(token_doc) is not EventTitleStyle.FULL
        )

    def _caseload(self, user_id: str) -> list[Patient]:
        """The therapist's patients, for working out initials.

        Fetched whole rather than a first page: two clients who collide on
        initials might sit on either side of a page boundary, and a
        collision that depends on pagination is a collision that shows up
        later, in production, as two identical labels.
        """
        if self._patient_repo is None:
            return []
        patients, _ = self._patient_repo.list_by_user(
            user_id, page=1, page_size=_CASELOAD_PAGE_SIZE
        )
        return list(patients)

    def _summary_for(
        self,
        user_id: str,
        appointment: Appointment,
        style: EventTitleStyle,
        *,
        caseload: list[Patient] | None = None,
    ) -> str:
        """What one appointment should read as on the calendar."""
        if style is EventTitleStyle.GENERIC or self._patient_repo is None:
            return _DEFAULT_EVENT_SUMMARY
        roster = self._caseload(user_id) if caseload is None else caseload
        patient = next((p for p in roster if p.id == appointment.patient_id), None)
        return summary_for(style, patient, initials=initials_by_patient(roster))

    def set_event_titling(
        self,
        user_id: str,
        style: EventTitleStyle,
        *,
        attested_account: str | None = None,
    ) -> bool:
        """Store how this connection's sessions should read. False if unconnected.

        ``attested_account`` is the account the therapist confirmed was
        covered, stored so a later connection to a different account stops
        honouring it rather than inheriting the permission.

        Narrowing is applied to events already pushed by a separate
        retitle pass, so the stored choice takes effect for new sessions
        immediately even if that pass has more to do.
        """
        token_doc = self._token_repo.get(user_id)
        if not token_doc:
            return False
        token_doc.event_titling = style.value
        if style is EventTitleStyle.FULL:
            token_doc.titling_attested_account = attested_account or ""
        self._token_repo.save(token_doc)
        logger.info("Calendar event titling set to %s", style.value)
        return True

    def retitle_future_events(self, user_id: str) -> RetitleOutcome:
        """Rewrite the titles of this connection's future events.

        Called when a therapist narrows what their calendar says. Without
        it the control is a lie: the setting would change while the names
        already written stayed sitting in Google, which is the disclosure
        they were trying to withdraw.

        Past events are left alone. They are a record of what happened,
        and rewriting history is not what was asked for.
        """
        credentials = self._get_credentials(user_id)
        token_doc = self._token_repo.get(user_id)
        if not credentials or not token_doc or not token_doc.calendar_id:
            return RetitleOutcome(0, 0, 0)

        style = self._effective_style(token_doc)
        caseload = self._caseload(user_id)
        now = _now()
        upcoming = [
            appointment
            for appointment in self._appointment_repo.list_by_range(
                user_id, now, now + timedelta(days=_RETITLE_HORIZON_DAYS)
            )
            if appointment.google_event_id
        ]
        capped = len(upcoming) > _RETITLE_MAX_EVENTS
        upcoming = upcoming[:_RETITLE_MAX_EVENTS]

        service = _build_calendar_service(credentials)
        retitled = 0
        failed = 0
        for appointment in upcoming:
            summary = self._summary_for(user_id, appointment, style, caseload=caseload)
            try:
                _patch_event_summary(
                    service, token_doc.calendar_id, str(appointment.google_event_id), summary
                )
            except Exception:
                # One event that won't take the change must not strand the
                # rest. The count comes back so a caller can say so and try
                # again — the pass is idempotent, so a retry is free.
                logger.warning("Could not retitle a Google Calendar event")
                failed += 1
                continue
            retitled += 1

        # HIPAA: counts only. What the events now say never reaches a log.
        logger.info(
            "Retitled %d Google Calendar event(s), %d could not be updated",
            retitled,
            failed,
        )
        return RetitleOutcome(
            retitled=retitled, failed=failed, skipped=len(upcoming) if capped else 0
        )

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
            client_id=token_data.get("client_id", self._surface.client_id),
            client_secret=token_data.get("client_secret", self._surface.client_secret),
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

    def _resolve_calendar_id(
        self,
        credentials: Credentials,
        write_target: CalendarWriteTarget,
    ) -> str:
        """Find the calendar this connection writes to, creating it if it's ours."""
        if write_target is CalendarWriteTarget.PRIMARY:
            return self._get_primary_calendar_id(credentials)
        return self._get_or_create_app_calendar_id(credentials)

    def _get_primary_calendar_id(self, credentials: Credentials) -> str:
        """Get the user's primary Google Calendar ID."""
        service = _build_calendar_service(credentials)
        calendar = service.calendars().get(calendarId="primary").execute()
        return calendar.get("id", "primary")  # type: ignore[no-any-return]

    def _get_or_create_app_calendar_id(self, credentials: Credentials) -> str:
        """Get the calendar Pablo owns on this account, creating it once.

        Under the app-calendar grant the calendar list only contains
        calendars this app created, so matching on the summary cannot pick
        up one of the therapist's own. Reconnecting finds the existing
        calendar rather than leaving a second one behind.
        """
        service = _build_calendar_service(credentials)
        listed = service.calendarList().list().execute()
        for entry in listed.get("items", []):
            if entry.get("summary") == _APP_CALENDAR_SUMMARY and entry.get("id"):
                logger.info("Reusing the existing Pablo-owned Google calendar")
                return str(entry["id"])

        created = service.calendars().insert(body={"summary": _APP_CALENDAR_SUMMARY}).execute()
        calendar_id = created.get("id")
        if not calendar_id:
            raise GoogleCalendarError("Google did not return an id for the created calendar")
        logger.info("Created a Pablo-owned Google calendar for session events")
        return str(calendar_id)

    @staticmethod
    def _appointment_to_event(
        appointment: Appointment, summary: str | None = None
    ) -> dict[str, Any]:
        """Map a Pablo appointment to a Google Calendar event body.

        ``summary`` is what the therapist chose this to read as. Without
        one it is the generic wording — a caller that hasn't worked out a
        title never accidentally sends a name.
        """
        event: dict[str, Any] = {
            "summary": summary or _DEFAULT_EVENT_SUMMARY,
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


def google_consent_surface(settings: Settings) -> ConsentSurface:
    """Read the deployment's Google Calendar credentials into a consent surface.

    Calendar has its own settings keys, and so its own OAuth client: which
    client a surface uses stays a deployment choice.
    """
    return ConsentSurface(
        provider_id=GOOGLE_PROVIDER_ID,
        client_id=settings.google_calendar_client_id,
        client_secret=settings.google_calendar_client_secret.get_secret_value(),
        allowed_capabilities=frozenset(GOOGLE_CAPABILITIES),
    )


def google_registration() -> ProviderRegistration:
    """Google's entry in the provider registry."""
    return ProviderRegistration(
        provider_id=GOOGLE_PROVIDER_ID,
        display_name=GoogleCalendarService.display_name,
        capabilities=GOOGLE_CAPABILITIES,
        consent_surface=google_consent_surface,
        build=GoogleCalendarService.from_surface,
    )
