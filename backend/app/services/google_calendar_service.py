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
- Pablo is source of truth for therapy appointments; only the time and the
  existence of a session Pablo pushed follow changes made in Google (see
  ``google_calendar_follow``)
"""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
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
from ..calendar_providers.oauth_state import mint_state, state_nonce, verify_state
from ..calendar_providers.pkce_store import remember_verifier, take_verifier
from ..calendar_providers.practice_import import (
    DEFAULT_HORIZON_DAYS,
    DEFAULT_LOOKBACK_DAYS,
    MAX_HORIZON_DAYS,
    build_proposal,
)
from ..calendar_providers.provider import BusyWindow, ConsentSurface, ImportCandidate
from ..calendar_providers.registry import ProviderRegistration
from ..meeting_providers.meet import CONFERENCE_SOLUTION_TYPE
from ..reliability import HTTP_REQUEST, Idempotency, call_with_retry
from ..repositories.google_calendar_token import (
    GoogleCalendarTokenDoc,
    GoogleCalendarTokenRepository,
)
from ..utcnow import utc_now, utc_now_iso
from .telehealth import GOOGLE_MEET
from .token_encryption import decrypt_tokens, derive_subkey, encrypt_tokens

if TYPE_CHECKING:
    from collections.abc import Callable, Collection

    from google.oauth2.credentials import Credentials

    from ..calendar_providers.practice_import import ImportProposal
    from ..models.patient import Patient
    from ..repositories.patient import PatientRepository
    from ..scheduling_engine.models.appointment import Appointment
    from ..scheduling_engine.repositories.appointment import AppointmentRepository
    from ..settings import Settings

logger = logging.getLogger(__name__)

GOOGLE_PROVIDER_ID = "google"


class PushedEvent(NamedTuple):
    """What a write to Google left us with.

    ``conference_url`` is None on every ordinary appointment and on one whose
    conference Google is still making — ``createRequest.status`` comes back
    ``pending`` sometimes, and a link that is not there yet is not a failure.
    The next push reads it.
    """

    event_id: str
    conference_url: str | None


def _conference_request_id(appointment: Appointment) -> str:
    """A stable idempotency key for the conference this appointment asks for.

    Derived from the appointment id so re-writing the same event re-uses the
    conference instead of making a second one. It goes to Google and nowhere
    near a patient, so the appointment id itself is the right value — unlike
    the handle in a room URL, which a patient is sent.
    """
    return f"pablo-{appointment.id}"


def conference_url(event: Mapping[str, Any]) -> str | None:
    """The video link on an event Google just handed back, if there is one.

    Two places carry it and they are not interchangeable. ``hangoutLink`` is
    the convenience field and is only ever a Meet URL; ``conferenceData``'s
    video entry point is the general one, and is what an event carries when
    the conference is not Google's own. Read the entry points first so the
    answer is right for both, and fall back to ``hangoutLink`` for an event
    old enough to carry only that.
    https://developers.google.com/workspace/calendar/api/v3/reference/events
    """
    data = event.get("conferenceData")
    if isinstance(data, Mapping):
        entry_points = data.get("entryPoints")
        if isinstance(entry_points, Sequence) and not isinstance(entry_points, str | bytes):
            for entry in entry_points:
                if isinstance(entry, Mapping) and entry.get("entryPointType") == "video":
                    uri = entry.get("uri")
                    if uri:
                        return str(uri)
    hangout = event.get("hangoutLink")
    return str(hangout) if hangout else None


# Writing to a calendar Google let Pablo create is reachable with a grant
# that cannot touch anything else on the account, so the narrowing is
# Google's to enforce and the wizard may say so.
_PUSH_TO_APP_CALENDAR = ProviderCapability(
    capability=CalendarCapability.PUSH,
    scopes=("https://www.googleapis.com/auth/calendar.app.created",),
    incremental=False,
    enforcement=NarrowingEnforcement.PROVIDER_ENFORCED,
    reach="the calendar Pablo creates",
)

# Writing to the therapist's own calendar has no such grant: the narrowest
# scope that can do it reaches every event on the calendar, so the limit is
# Pablo's discipline and the copy has to say that instead.
_PUSH_TO_PRIMARY = ProviderCapability(
    capability=CalendarCapability.PUSH,
    scopes=("https://www.googleapis.com/auth/calendar.events",),
    incremental=False,
    enforcement=NarrowingEnforcement.PABLO_ENFORCED,
    reach="adding, updating and removing sessions booked in Pablo",
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
    reach="busy times; event titles and guests are not shared",
)

_IMPORT = ProviderCapability(
    capability=CalendarCapability.IMPORT,
    scopes=("https://www.googleapis.com/auth/calendar.readonly",),
    incremental=True,
    enforcement=NarrowingEnforcement.PABLO_ENFORCED,
    reach="reading the calendar you choose to import from or follow",
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

# The calendar an import scan reads. Google resolves "primary" to the
# account's own calendar, which is what the IMPORT grant reaches.
_IMPORT_CALENDAR_ID = "primary"

#: What following "the main calendar" stores until a read resolves it to the
#: calendar's real id. Google accepts it as an id on every call.
FOLLOW_MAIN_CALENDAR = "primary"

# The private property every event Pablo writes carries, naming the
# appointment behind it.
_PABLO_APPOINTMENT_KEY = "pablo_appointment_id"

_HTTP_OK = 200
# Google answers a syncToken it no longer honours with 410 Gone.
_HTTP_GONE = 410
# And a calendar that no longer exists with 404.
_HTTP_NOT_FOUND = 404
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

    events: list[dict[str, Any]]
    next_sync_token: str | None
    page_count: int

    @property
    def changes(self) -> list[dict[str, Any]]:
        return [_event_to_change(event) for event in self.events]


def _http_status(exc: Exception) -> int | None:
    """The HTTP status behind a Google API error, if it carries one.

    Duck-typed rather than caught by class: googleapiclient is a lazy
    import here, and its HttpError exposes the status two different ways
    depending on version.
    """
    status = getattr(getattr(exc, "resp", None), "status", None)
    if status is None:
        status = getattr(exc, "status_code", None)
    return status if isinstance(status, int) else None


def _is_expired_sync_token(exc: Exception) -> bool:
    """Report whether an API error is Google's expired-syncToken 410."""
    return _http_status(exc) == _HTTP_GONE


def _event_to_change(event: dict[str, Any]) -> dict[str, Any]:
    """Map a Google event to the change dict the sync scheduler consumes."""
    return {
        "google_event_id": event.get("id"),
        "summary": event.get("summary", ""),
        "start": event.get("start", {}),
        "end": event.get("end", {}),
        "status": event.get("status", ""),
    }


def _is_pablos_own(event: Mapping[str, Any]) -> bool:
    private = (event.get("extendedProperties") or {}).get("private") or {}
    return bool(private.get(_PABLO_APPOINTMENT_KEY))


def _declined(event: Mapping[str, Any]) -> bool:
    """Whether the calendar's owner said no to this event."""
    return any(
        attendee.get("self") and attendee.get("responseStatus") == "declined"
        for attendee in event.get("attendees") or []
    )


def _main_calendar_change(event: dict[str, Any]) -> dict[str, Any] | None:
    """A change on the clinician's own calendar, for following outside sessions.

    None for an event Pablo wrote itself (followed as Pablo's own) and for an
    all-day event, which is never a session. A declined event reads as gone:
    the clinician is not going. A deletion carries no times or properties, so
    it passes as a deletion and whoever holds its id decides what it was.
    """
    if _is_pablos_own(event):
        return None
    change = _event_to_change(event)
    change["series_id"] = event.get("recurringEventId")
    if change["status"] == "cancelled":
        return change
    if _declined(event):
        change["status"] = "cancelled"
        return change
    if parse_event_time(change["start"]) is None or parse_event_time(change["end"]) is None:
        return None
    return change


class MainCalendarRead(NamedTuple):
    """One read of the calendar the clinician follows."""

    changes: list[dict[str, Any]]
    full: bool
    """Read from scratch rather than resumed. A full read holds every event
    in its ``window``, so anything in that window missing from it is gone —
    and deletions from before it are never reported on their own."""
    window: tuple[datetime, datetime] | None = None
    """What a full read covered: ``[start, end)``. Nothing outside it can be
    judged from the read."""
    calendar_id: str | None = None
    """The calendar read, by its real id. Only its own rows and sessions can
    be judged from the read."""
    main_calendar_id: str | None = None
    """The main calendar's real id, when this read learned it."""


class ReadableCalendar(NamedTuple):
    """A calendar the connection can read, offered to be followed."""

    id: str
    name: str
    primary: bool


class CalendarScopeNotGrantedError(Exception):
    """Google's answer is missing a permission this request asked for."""


class CalendarGoneError(Exception):
    """The calendar Pablo made on this account no longer exists.

    Raised by an inbound sync rather than folded into an empty change list,
    because the two mean opposite things: no changes leaves every session
    where it is, while a vanished calendar means every event went at once
    and nothing about any one session has been decided.
    """


def _exchange_code(flow: Any, code: str, requested_scopes: Sequence[str]) -> None:
    """Trade the authorization code for tokens, accepting a wider grant.

    Google answers an incremental request (``include_granted_scopes``) with
    every permission the account has granted this app, not only the ones
    just asked for — including grants from earlier connects. oauthlib treats
    any difference from the requested scopes as an error ("Scope has
    changed"), which fails exactly the request designed to add to what is
    already held. So the exchange runs without an expected scope, and the
    check that matters is made here instead: everything asked for must be
    in what Google granted. A therapist who unticks the new permission on
    Google's screen is refused, rather than recorded as having granted it.

    The session's scope goes back afterwards because the credentials are
    built from it.
    """
    session = flow.oauth2session
    expected = session.scope
    session.scope = None
    try:
        flow.fetch_token(code=code)
    finally:
        session.scope = expected

    granted = session.token.get("scope")
    if isinstance(granted, str):
        granted_set = set(granted.split())
    elif isinstance(granted, list | tuple | set | frozenset):
        granted_set = {str(scope) for scope in granted}
    else:
        # Google always names the scopes; a response without them cannot
        # be checked, and is taken as the grant that was asked for.
        return
    missing = [scope for scope in requested_scopes if scope not in granted_set]
    if missing:
        raise CalendarScopeNotGrantedError("Google did not grant: " + ", ".join(sorted(missing)))


_GOOGLE_AUTH_URI = "https://accounts.google.com/o/oauth2/auth"
_GOOGLE_TOKEN_URI = "https://oauth2.googleapis.com/token"  # noqa: S105 — an endpoint, not a secret
_GOOGLE_REVOKE_URI = "https://oauth2.googleapis.com/revoke"

# Where a stand-in (``ConsentSurface.base_url``) answers each of Google's
# three hosts. The paths are Google's own, so one origin can serve all three.
_STAND_IN_AUTH_PATH = "/o/oauth2/auth"
_STAND_IN_TOKEN_PATH = "/token"  # noqa: S105 — an endpoint, not a secret
_STAND_IN_REVOKE_PATH = "/revoke"
_REVOKE_TIMEOUT_SECONDS = 10.0
_STAND_IN_API_PATH = "/calendar/v3/"


def _build_flow(
    client_id: str,
    client_secret: str,
    redirect_uri: str,
    scopes: Sequence[str],
    *,
    base_url: str | None = None,
) -> Any:
    """Lazily import and construct a google_auth_oauthlib Flow.

    ``base_url`` points both OAuth endpoints at a stand-in for Google; the
    token URI it hands out is stored with the tokens, so refreshes go there
    too.
    """
    from google_auth_oauthlib.flow import Flow

    origin = base_url.rstrip("/") if base_url else None
    return Flow.from_client_config(
        {
            "web": {
                "client_id": client_id,
                "client_secret": client_secret,
                "auth_uri": f"{origin}{_STAND_IN_AUTH_PATH}" if origin else _GOOGLE_AUTH_URI,
                "token_uri": f"{origin}{_STAND_IN_TOKEN_PATH}" if origin else _GOOGLE_TOKEN_URI,
            }
        },
        scopes=list(scopes),
        redirect_uri=redirect_uri,
    )


def _build_calendar_service(credentials: Any, *, base_url: str | None = None) -> Any:
    """Lazily import and build a Google Calendar API service.

    The client is built from the discovery document the library ships, so
    nothing is fetched to construct it. ``base_url`` replaces where every
    call then goes — the library's own seam for that, ``api_endpoint`` — so
    a stand-in is reached by the same request code as Google is.
    """
    from googleapiclient.discovery import build  # type: ignore[import-untyped,import-not-found]

    client_options = (
        {"api_endpoint": f"{base_url.rstrip('/')}{_STAND_IN_API_PATH}"} if base_url else None
    )
    return build("calendar", "v3", credentials=credentials, client_options=client_options)


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


def _revoke_grant(token: str, *, base_url: str | None = None) -> bool:
    """Ask Google to withdraw the grant this token belongs to.

    Revoking either token of a grant revokes the whole grant, so Pablo drops
    off the account's third-party access list. Whether it worked never
    decides whether the disconnect happens — the caller deletes its copy of
    the tokens either way — so every failure is logged and swallowed: an
    unreachable Google, a 400 for a grant the user already removed there.
    The token itself never reaches the log.
    https://developers.google.com/identity/protocols/oauth2/web-server#tokenrevoke
    """
    import httpx

    url = f"{base_url.rstrip('/')}{_STAND_IN_REVOKE_PATH}" if base_url else _GOOGLE_REVOKE_URI
    try:
        response = httpx.post(
            url,
            data={"token": token},
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=_REVOKE_TIMEOUT_SECONDS,
        )
    except httpx.HTTPError as exc:
        logger.warning("Google Calendar revoke could not reach Google: %s", type(exc).__name__)
        return False
    if response.status_code != _HTTP_OK:
        logger.warning("Google Calendar revoke answered %s", response.status_code)
        return False
    return True


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

    Not a failure: BUSY is an opt-in choice at connect ("Check for
    scheduling conflicts"), never asked for incrementally, so a connection that
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


def _resolve_main_calendar(service: Any) -> str | None:
    """The main calendar's real id, from the calendar list.

    Asked of the calendar list, which the grant to read events covers. The
    id is what a followed ``primary`` is stored as and what the import keys
    its answers by. None when nothing comes back: ``primary`` is the same
    word for every account, so it never stands in for an id.
    """
    resolved: dict[str, Any] = _with_calendar_retry(
        lambda: service.calendarList().get(calendarId=FOLLOW_MAIN_CALENDAR).execute()
    )
    calendar_id = resolved.get("id")
    return str(calendar_id) if calendar_id else None


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
    sessions, so they drop out here. So does any event Pablo wrote itself:
    when sessions go to the therapist's own calendar they sit beside the
    practice being imported, and proposing them would book each twice.
    """
    private = event.get("extendedProperties", {}).get("private", {})
    if private.get(_PABLO_APPOINTMENT_KEY):
        return None
    start = parse_event_time(event.get("start", {}))
    end = parse_event_time(event.get("end", {}))
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


def parse_event_time(slot: dict[str, Any]) -> datetime | None:
    """A Google event boundary as an instant; None for an all-day date."""
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
            base_url=self._surface.base_url,
        )
        # A request made entirely of capabilities the provider declares
        # incremental is one asked for later, alongside grants already held —
        # so it has to add to them rather than replace them. A connect-time
        # request must NOT: there, the selection is the whole answer, and
        # carrying old grants forward would stop a therapist narrowing one.
        incremental = bool(requested) and all(
            declarations[capability].incremental for capability in requested
        )
        state = mint_state(derive_subkey(_STATE_PURPOSE), user_id)
        auth_url, _ = flow.authorization_url(
            access_type="offline",
            prompt="consent",
            state=state,
            include_granted_scopes="true" if incremental else "false",
        )
        # authorization_url() generated a PKCE verifier and sent Google the
        # challenge derived from it. It lives on this Flow, which does not
        # outlive this request, so the exchange gets it from here instead —
        # without it Google rejects the code as "Missing code verifier".
        remember_verifier(state_nonce(state), flow.code_verifier)
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
        # Taken only after the state verifies, and taken exactly once — so a
        # replayed state finds nothing here even while it is still inside its
        # own expiry window.
        verifier = take_verifier(state_nonce(state))
        requested = self._resolve_request(capabilities)
        declarations = google_capabilities(write_target)
        scopes = scopes_for(declarations, requested)
        flow = _build_flow(
            self._surface.client_id,
            self._surface.client_secret,
            redirect_uri,
            scopes,
            base_url=self._surface.base_url,
        )
        flow.code_verifier = verifier
        _exchange_code(flow, code, scopes)
        credentials = flow.credentials

        token_data = {
            "token": credentials.token or "",
            "refresh_token": credentials.refresh_token or "",
            "token_uri": credentials.token_uri or "",
            "client_id": credentials.client_id or "",
            "client_secret": credentials.client_secret or "",
        }

        encrypted = encrypt_tokens(token_data)

        self._record_existing_app_calendar(user_id)
        calendar_id = self._resolve_calendar_id(credentials, write_target, user_id)
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
        pushed = self.push_appointment_event(user_id, appointment)
        return pushed.event_id if pushed else None

    def push_appointment_event(self, user_id: str, appointment: Appointment) -> PushedEvent | None:
        """Push the appointment, and say what came back about its conference.

        Same call as :meth:`push_appointment` — this is the whole return value
        rather than one field of it. Separate because the conference link only
        exists on the way back from Google: the event body ASKS for a Meet
        conference and Google answers with the link, so an appointment on
        ``google_meet`` learns its own room here and nowhere else.

        ``conferenceDataVersion=1`` is what makes that ask mean anything. Sent
        on every write rather than only when a conference is requested,
        because the flag is also what stops an update dropping the conference
        from an event that already has one.
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
        service = self._calendar(credentials)

        if appointment.google_event_id:
            event = (
                service.events()
                .update(
                    calendarId=token_doc.calendar_id,
                    eventId=appointment.google_event_id,
                    body=event_body,
                    conferenceDataVersion=1,
                )
                .execute()
            )
            logger.info("Updated Google Calendar event")
        else:
            event = (
                service.events()
                .insert(
                    calendarId=token_doc.calendar_id,
                    body=event_body,
                    conferenceDataVersion=1,
                )
                .execute()
            )
            logger.info("Created Google Calendar event")

        event_id = event.get("id")
        if not event_id:
            return None
        return PushedEvent(event_id=str(event_id), conference_url=conference_url(event))

    def delete_event(self, user_id: str, event_id: str) -> bool:
        """Delete a Google Calendar event."""
        credentials = self._get_credentials(user_id)
        if not credentials:
            return False

        token_doc = self._token_repo.get(user_id)
        if not token_doc or not token_doc.calendar_id:
            return False

        service = self._calendar(credentials)
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

        Returns a list of change dicts for the caller to process. Raises
        :class:`CalendarGoneError` when the calendar Pablo made was deleted.
        """
        credentials = self._get_credentials(user_id)
        if not credentials:
            return []

        token_doc = self._token_repo.get(user_id)
        if not token_doc or not token_doc.calendar_id:
            return []

        service = self._calendar(credentials)

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
                len(page.events),
                page.page_count,
            )
        except Exception as exc:
            if (
                _http_status(exc) == _HTTP_NOT_FOUND
                and token_doc.write_target == CalendarWriteTarget.APP_CALENDAR.value
            ):
                raise CalendarGoneError from exc
            # HIPAA: don't log response bodies that might contain PHI
            logger.exception("Google Calendar sync failed")
            return []

        return page.changes

    def recreate_app_calendar(self, user_id: str) -> bool:
        """Make the Pablo calendar again after it was deleted in Google.

        Forgets the sync token too: it belonged to the old calendar, and the
        next poll should read the new one from scratch. Returns whether a
        calendar is in place to push to. A connection to the therapist's own
        calendar has nothing of Pablo's to recreate.
        """
        credentials = self._get_credentials(user_id)
        token_doc = self._token_repo.get(user_id)
        if (
            not credentials
            or not token_doc
            or token_doc.write_target != CalendarWriteTarget.APP_CALENDAR.value
        ):
            return False
        token_doc.calendar_id = self._get_or_create_app_calendar_id(credentials, user_id)
        token_doc.sync_token = None
        self._token_repo.save(token_doc)
        logger.info("Recreated the Pablo-owned Google calendar after it was deleted")
        return True

    def read_event_times(
        self, user_id: str, event_id: str, *, followed_calendar: str | None = None
    ) -> tuple[datetime, datetime] | None:
        """Where Google has an event now, or None if it can't say.

        One of Pablo's own by default; ``followed_calendar`` reads a followed
        session on that calendar instead. None covers an event that is gone,
        an all-day event, and a connection that is not there any more — none
        of them is a time to move to.
        """
        credentials = self._get_credentials(user_id)
        token_doc = self._token_repo.get(user_id)
        if not credentials or not token_doc or not token_doc.calendar_id:
            return None
        calendar_id = followed_calendar or token_doc.calendar_id
        service = self._calendar(credentials)
        try:
            event = _read_event(service, calendar_id, event_id)
        except Exception:
            logger.warning("Could not read a Google Calendar event")
            return None
        if event.get("status") == "cancelled":
            return None
        start = parse_event_time(event.get("start", {}))
        end = parse_event_time(event.get("end", {}))
        if start is None or end is None:
            return None
        return start, end

    def can_read_events(self, user_id: str) -> bool:
        """Whether the connection holds the grant to read event content."""
        token_doc = self._token_repo.get(user_id)
        granted = token_doc.granted_capabilities if token_doc else ""
        return CalendarCapability.IMPORT.value in _split_capabilities(granted)

    def list_readable_calendars(self, user_id: str) -> list[ReadableCalendar]:
        """The calendars this connection can read, the main one first.

        Needs the grant to read events (``calendar.readonly``, which covers
        the calendar list); without it there is nothing to offer.
        """
        if not self.can_read_events(user_id):
            return []
        credentials = self._get_credentials(user_id)
        if not credentials:
            return []
        service = self._calendar(credentials)
        found: list[ReadableCalendar] = []
        page_token: str | None = None
        while True:
            kwargs: dict[str, Any] = {"minAccessRole": "reader"}
            if page_token:
                kwargs["pageToken"] = page_token
            request = service.calendarList().list(**kwargs)
            page: dict[str, Any] = _with_calendar_retry(request.execute)
            for item in page.get("items", []):
                calendar_id = str(item.get("id") or "")
                if not calendar_id or item.get("deleted"):
                    continue
                found.append(
                    ReadableCalendar(
                        id=calendar_id,
                        name=str(item.get("summaryOverride") or item.get("summary") or ""),
                        primary=bool(item.get("primary")),
                    )
                )
            page_token = page.get("nextPageToken")
            if not page_token:
                break
        return sorted(found, key=lambda c: (not c.primary, c.name.lower()))

    def set_followed_calendar(
        self, user_id: str, calendar_id: str | None, *, main_calendar: bool = False
    ) -> bool:
        """Follow this calendar, or none. Returns whether that changed anything.

        Choosing another calendar starts its read over, from now: whatever an
        earlier stretch of following left behind is not replayed. The caller
        checks the id is one the connection can read, and says whether it is
        the main calendar.
        """
        token_doc = self._token_repo.get(user_id)
        current = token_doc.follow_calendar_id if token_doc else None
        if calendar_id == current:
            return False
        if calendar_id is not None:
            self._token_repo.update_main_calendar_sync_token(user_id, None)
        self._token_repo.set_followed_calendar(
            user_id, calendar_id, main_calendar=main_calendar or calendar_id == FOLLOW_MAIN_CALENDAR
        )
        return True

    def remember_followed_calendar_id(self, user_id: str, calendar_id: str) -> None:
        """Store the main calendar's real id in place of ``primary``; the read carries on."""
        self._token_repo.resolve_followed_main_calendar(user_id, calendar_id)

    def known_main_calendar_id(self, user_id: str) -> str | None:
        """The main calendar's real id, when a read has already learned it.

        Known once the clinician follows the main calendar and a read has
        resolved ``primary`` to its id; nothing is asked of the provider. A
        clinician following another calendar, or none, gets None.
        """
        token_doc = self._token_repo.get(user_id)
        if (
            token_doc is None
            or not token_doc.follows_main_calendar
            or not token_doc.follow_calendar_id
            or token_doc.follow_calendar_id == FOLLOW_MAIN_CALENDAR
        ):
            return None
        return token_doc.follow_calendar_id

    def main_calendar_id(self, user_id: str) -> str | None:
        """The main calendar's real id, asked of the provider.

        The import reads the main calendar, and what it remembers is keyed
        by that calendar's id — the same id a read of a followed ``primary``
        resolves to, so the import and following remember one calendar under
        one name. Needs the grant to read events; None without it, or when
        Google names no id for it.
        """
        if not self.can_read_events(user_id):
            return None
        credentials = self._get_credentials(user_id)
        if not credentials:
            return None
        return _resolve_main_calendar(self._calendar(credentials))

    def read_main_calendar_changes(self, user_id: str) -> MainCalendarRead:
        """What changed on the followed calendar since the last read.

        Read only — nothing here writes to that calendar. Resumes from its own
        sync token, separate from the one of the calendar Pablo writes to, and
        leaves out Pablo's own events and all-day events. Needs the grant to
        read event content; without it, or with nothing followed, there is
        nothing to read.

        A followed ``primary`` is resolved to the main calendar's real id and
        stored, keeping its sync token: it is the same calendar.

        Without a token (the first read, or Google aged the token out) the
        read starts over from now, and says so: see ``MainCalendarRead.full``.
        """
        nothing = MainCalendarRead([], full=False)
        if not self.can_read_events(user_id):
            return nothing
        credentials = self._get_credentials(user_id)
        token_doc = self._token_repo.get(user_id)
        if not credentials or not token_doc or not token_doc.follow_calendar_id:
            return nothing
        service = self._calendar(credentials)
        calendar_id = token_doc.follow_calendar_id
        main_calendar_id = None
        # What the rows read here are recorded under. None when Google named
        # no id for the main calendar: the read still asks for ``primary``,
        # but ``primary`` is the same word for every account, so nothing is
        # recorded under it. The rows stay unrecorded until a read learns the
        # real id, and are claimed onto it then.
        recorded_as: str | None = calendar_id
        if calendar_id == FOLLOW_MAIN_CALENDAR:
            main_calendar_id = _resolve_main_calendar(service)
            recorded_as = main_calendar_id
            if main_calendar_id is not None:
                calendar_id = main_calendar_id
                if not self._token_repo.resolve_followed_main_calendar(user_id, calendar_id):
                    # Another calendar was chosen while this read was starting;
                    # the next read follows that one.
                    return nothing
        full = token_doc.main_calendar_sync_token is None
        # Bounded like an import scan: past it, Google's expansion of a
        # repeating event may stop, and a missing instance proves nothing.
        start = _now()
        window = (start, start + timedelta(days=MAX_HORIZON_DAYS))
        try:
            page = self._list_all_events(
                service,
                calendar_id,
                sync_token=token_doc.main_calendar_sync_token,
                window=window,
            )
        except Exception as exc:
            if not _is_expired_sync_token(exc):
                raise
            logger.info("Followed calendar sync token expired; re-reading from a fresh window")
            self._token_repo.update_main_calendar_sync_token(user_id, None)
            page = self._list_all_events(service, calendar_id, sync_token=None, window=window)
            full = True
        if page.next_sync_token:
            self._token_repo.update_main_calendar_sync_token(user_id, page.next_sync_token)
        changes = [
            change
            for change in (_main_calendar_change(event) for event in page.events)
            if change is not None
        ]
        # HIPAA: counts only.
        logger.info(
            "Read %d followed calendar changes over %d page(s)", len(changes), page.page_count
        )
        return MainCalendarRead(
            changes,
            full=full,
            window=window if full else None,
            calendar_id=recorded_as,
            main_calendar_id=main_calendar_id,
        )

    @staticmethod
    def _list_all_events(
        service: Any,
        calendar_id: str,
        *,
        sync_token: str | None,
        window: tuple[datetime, datetime] | None = None,
    ) -> _EventPage:
        """Walk every page of events().list, collecting changes and the sync token.

        Google splits large result sets across pages and only returns
        nextSyncToken on the final one, so reading a single page both drops
        changes and leaves the next poll with nothing to resume from.

        ``window`` bounds a read without a token; a resumed read takes none.
        """
        kwargs: dict[str, Any] = {
            "calendarId": calendar_id,
            "singleEvents": True,
            "maxResults": _SYNC_PAGE_SIZE,
        }
        if sync_token:
            kwargs["syncToken"] = sync_token
        elif window is not None:
            kwargs["timeMin"] = window[0].isoformat()
            kwargs["timeMax"] = window[1].isoformat()
        else:
            # First sync: only get future events
            kwargs["timeMin"] = utc_now_iso()

        events: list[dict[str, Any]] = []
        page_count = 0
        while True:
            result = service.events().list(**kwargs).execute()
            page_count += 1
            events.extend(result.get("items", []))

            page_token = result.get("nextPageToken")
            if not page_token:
                return _EventPage(events, result.get("nextSyncToken"), page_count)
            kwargs["pageToken"] = page_token

    def disconnect(self, user_id: str) -> bool:
        """Withdraw the grant at Google and remove the stored tokens.

        The refresh token is the one revoked when there is one: it is what
        keeps the grant alive, and revoking it takes the access token with it.
        A revoke that fails still disconnects (see ``_revoke_grant``).

        The calendar Pablo made is remembered apart from the tokens, so a
        later connect finds it rather than making another one. Following a
        calendar is turned off, so a reconnect starts without it. What Pablo
        read from the calendar is the caller's to remove, in the same
        transaction — see ``app.calendar_providers.disconnect``.
        """
        self._record_existing_app_calendar(user_id)
        token = self._stored_grant_token(user_id)
        if token:
            _revoke_grant(token, base_url=self._surface.base_url)
        deleted = self._token_repo.delete(user_id)
        if deleted:
            # Following is a choice made about this connection. Left on, a
            # reconnect would start reading the calendar again without being
            # asked, after the clinician said to stop.
            self._token_repo.set_followed_calendar(user_id, None)
            logger.info("Google Calendar disconnected")
        return deleted

    def _stored_grant_token(self, user_id: str) -> str | None:
        """The token to revoke the grant with, or None if there is none to read.

        Tokens that no longer decrypt (a rotated key, a damaged row) leave
        nothing to revoke with; the disconnect goes ahead without it.
        """
        token_doc = self._token_repo.get(user_id)
        if token_doc is None:
            return None
        try:
            token_data = decrypt_tokens(token_doc.encrypted_tokens)
        except Exception:
            logger.warning("Google Calendar tokens did not decrypt; disconnecting without revoke")
            return None
        token = token_data.get("refresh_token") or token_data.get("token")
        return str(token) if token else None

    def get_sync_status(self, user_id: str) -> dict[str, Any]:
        """Check connection status, last sync time, and what was granted."""
        token_doc = self._token_repo.get(user_id)
        if not token_doc:
            return {
                "connected": False,
                "calendar_id": None,
                "calendar_name": None,
                "last_synced_at": None,
                "write_target": None,
                "busy": None,
                "event_titling": None,
                "titling_needs_attestation": False,
                "follow_calendar_id": None,
                "import_granted": False,
            }
        return {
            "connected": True,
            "calendar_id": token_doc.calendar_id,
            # Only the app calendar needs one: its id is an opaque
            # ...@group.calendar.google.com hash that means nothing to the
            # therapist reading it. A primary connection's id is their own
            # email address, which is already the best label for it.
            "calendar_name": (
                _APP_CALENDAR_SUMMARY
                if token_doc.write_target == CalendarWriteTarget.APP_CALENDAR.value
                else None
            ),
            "last_synced_at": token_doc.last_synced_at,
            "write_target": token_doc.write_target,
            "busy": CalendarCapability.BUSY.value
            in _split_capabilities(token_doc.granted_capabilities),
            "event_titling": self._effective_style(token_doc).value,
            "titling_needs_attestation": self._needs_reattestation(token_doc),
            "follow_calendar_id": token_doc.follow_calendar_id,
            "import_granted": CalendarCapability.IMPORT.value
            in _split_capabilities(token_doc.granted_capabilities),
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

        service = self._calendar(credentials)
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
        """Read every occurrence in the window. Returns (occurrences, truncated).

        Reads the therapist's own calendar, not the one PUSH writes to. The
        practice being imported lives where the therapist already keeps it;
        a calendar Pablo made holds only what Pablo put there, so scanning
        it proposes nothing.
        """
        credentials = self._get_credentials(user_id)
        if not credentials:
            return [], False

        service = self._calendar(credentials)
        kwargs: dict[str, Any] = {
            "calendarId": _IMPORT_CALENDAR_ID,
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
        if not credentials:
            return {}

        service = self._calendar(credentials)
        rules: dict[str, list[str]] = {}
        for series_id in series_ids:
            try:
                master = _read_event(service, _IMPORT_CALENDAR_ID, series_id)
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

        service = self._calendar(credentials)
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

    def _calendar(self, credentials: Credentials) -> Any:
        """A Calendar API client for these credentials, at wherever this surface points."""
        return _build_calendar_service(credentials, base_url=self._surface.base_url)

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
        user_id: str,
    ) -> str:
        """Find the calendar this connection writes to, creating it if it's ours."""
        if write_target is CalendarWriteTarget.PRIMARY:
            return self._get_primary_calendar_id(credentials)
        return self._get_or_create_app_calendar_id(credentials, user_id)

    def _get_primary_calendar_id(self, credentials: Credentials) -> str:
        """Get the user's primary Google Calendar ID.

        Asked of the events list, not ``calendars().get``: a main-calendar
        connection holds ``calendar.events`` alone, which may read and write
        events but not calendar metadata, so ``calendars().get`` answers 403
        "insufficient authentication scopes" and the connect fails. An events
        list names its calendar in ``summary`` — the account's address, for a
        primary calendar. ``"primary"`` is itself a valid id for every call
        Pablo makes, so it stands in when no name comes back.
        """
        service = self._calendar(credentials)
        listing = (
            service.events().list(calendarId="primary", maxResults=1, fields="summary").execute()
        )
        return str(listing.get("summary") or "primary")

    def _record_existing_app_calendar(self, user_id: str) -> None:
        """Remember an app calendar connected before calendars were recorded.

        Such a connection's calendar is known only from its token row, which
        a disconnect deletes and a main-calendar connect overwrites. Copying
        it across first, from the clinician's own row, is what lets those
        connections reuse it too. A no-op once anything is recorded.
        """
        stored = self._token_repo.get(user_id)
        if (
            stored is not None
            and stored.calendar_id
            and stored.write_target == CalendarWriteTarget.APP_CALENDAR.value
            and not self._token_repo.get_app_calendar_id(user_id)
        ):
            self._token_repo.remember_app_calendar_id(user_id, stored.calendar_id)

    def _get_or_create_app_calendar_id(self, credentials: Credentials, user_id: str) -> str:
        """Get the calendar Pablo owns on this account, creating it once.

        Reconnecting finds the existing calendar rather than leaving a second
        one behind — but it finds it in our own records, not by asking for a
        list. The app-calendar grant is a single scope,
        ``calendar.app.created``, and Google refuses ``calendarList.list``
        under it: this used to open with that call, so the connect could never
        finish. Its identity is already ours to remember, so remember it — in
        the clinician's calendar settings rather than on the connection,
        because disconnecting deletes the connection and a main-calendar
        connect points it elsewhere, and either used to leave the next
        app-calendar connect nothing to reuse. Only an id Pablo's own insert returned is recorded,
        never a calendar matched by name: one the therapist made and happened
        to call "Pablo Sessions" is theirs, not ours.

        A remembered id still has to be checked, because the record outlives
        the account that id belongs to. ``handle_callback`` runs whenever a
        connect completes, including one that never disconnected first, so a
        therapist reconnecting a DIFFERENT Google account would otherwise
        inherit a calendar living in the previous one — credentials that
        cannot write there, and a push that fails on every appointment after.
        ``calendars().get`` answers that, and doubles as the existence check
        for a calendar deleted on Google's side.

        Any error from that check means create a new one. Being wrong in that
        direction leaves a stray calendar; being wrong in the other silently
        points a connection at somebody else's.
        """
        service = self._calendar(credentials)

        remembered = self._token_repo.get_app_calendar_id(user_id)
        if remembered:
            try:
                service.calendars().get(calendarId=remembered).execute()
            except Exception:
                # Deleted, or owned by an account these credentials do not
                # speak for. Either way the remembered id is not usable now.
                logger.info("Stored Pablo-owned calendar is unreachable; creating a new one")
            else:
                logger.info("Reusing the existing Pablo-owned Google calendar")
                return remembered

        created = service.calendars().insert(body={"summary": _APP_CALENDAR_SUMMARY}).execute()
        calendar_id = created.get("id")
        if not calendar_id:
            raise GoogleCalendarError("Google did not return an id for the created calendar")
        self._token_repo.remember_app_calendar_id(user_id, str(calendar_id))
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

        ``conferenceData`` carries one of two opposite things, and which one
        depends on whether there is already a link:

        * a link the appointment has — written out as an entry point so the
          therapist's calendar shows the room they are joining;
        * a ``createRequest`` — asking Google to MAKE a Meet conference,
          which it does when the event is written with
          ``conferenceDataVersion=1``, and whose link is read back onto the
          appointment afterwards.

        The request id is derived from the appointment so a retry of the same
        write re-uses the same conference instead of making a second one.
        Google documents it as the caller's idempotency key for exactly this.
        https://developers.google.com/workspace/calendar/api/v3/reference/events
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
                    _PABLO_APPOINTMENT_KEY: appointment.id,
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
        elif appointment.provider == GOOGLE_MEET:
            event["conferenceData"] = {
                "createRequest": {
                    "requestId": _conference_request_id(appointment),
                    "conferenceSolutionKey": {"type": CONFERENCE_SOLUTION_TYPE},
                }
            }
        return event


def google_consent_surface(settings: Settings) -> ConsentSurface:
    """Read the deployment's Google Calendar credentials into a consent surface.

    Calendar has its own settings keys, and so its own OAuth client: which
    client a surface uses stays a deployment choice.

    A stand-in for Google (``google_calendar_base_url``) is honoured only in
    a development environment. Anywhere else the setting is ignored rather
    than refused, so a deployment that carries it by mistake still talks to
    Google and never sends a clinician's calendar to whatever else answers
    at that address.
    """
    return ConsentSurface(
        provider_id=GOOGLE_PROVIDER_ID,
        client_id=settings.google_calendar_client_id,
        client_secret=settings.google_calendar_client_secret.get_secret_value(),
        allowed_capabilities=frozenset(GOOGLE_CAPABILITIES),
        base_url=settings.google_calendar_base_url if settings.is_development else None,
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
