# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""A stand-in for Google — OAuth and Calendar v3 — for the end-to-end stack.

One Google account with a main calendar, reached through the same client
code a deployment uses: the backend's ``GOOGLE_CALENDAR_BASE_URL`` points
``google_auth_oauthlib`` and ``googleapiclient`` here, so a connect, an
import scan, a followed read and a pushed session all travel the real
request path and only the far end is different. The three hosts Google
splits this over answer from one origin under their own paths.

Two surfaces, each only as wide as the backend relies on:

* **OAuth** — ``/o/oauth2/auth`` grants at once (there is no consent screen
  to click) and redirects back with a code; ``/token`` exchanges it, checks
  PKCE, and refreshes. An incremental request (``include_granted_scopes``)
  answers with the union of the grants held, as Google does, so the
  backend's own check that everything asked for was granted is exercised.
* **Calendar v3** — the calendar list, calendars, events (with
  ``singleEvents`` expansion of a recurring series, ``timeMin``/``timeMax``,
  pages, and ``syncToken`` incremental reads that answer an expired token
  with 410), and free/busy. Every call is checked against the token's
  scopes: a grant that reaches only the calendar the app made cannot list
  the account's calendars, exactly the refusals the backend was written
  around.

Below ``/_fake/`` a spec puts events on a calendar, moves or retitles them,
deletes them, adds a second calendar, expires the sync tokens, reads what
the backend pushed, and reads what was granted. Everything lives in memory.

Run with ``uvicorn fake_google:app --port 8090``; the compose stack builds
it from ``scripts/e2e/fake-google.Dockerfile``.
"""

from __future__ import annotations

import base64
import hashlib
import os
import secrets
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from itertools import islice
from typing import TYPE_CHECKING, Any
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

from dateutil.rrule import rrulestr
from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse, RedirectResponse

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

CLIENT_ID = os.environ.get("FAKE_GOOGLE_CLIENT_ID", "e2e-google-client-id")
CLIENT_SECRET = os.environ.get("FAKE_GOOGLE_CLIENT_SECRET", "e2e-google-client-secret")
DEFAULT_ACCOUNT = os.environ.get("FAKE_GOOGLE_ACCOUNT", "clinician@example.test")
ACCOUNT_TIME_ZONE = "America/New_York"

_SCOPE = "https://www.googleapis.com/auth/"
CALENDAR = _SCOPE + "calendar"
READONLY = _SCOPE + "calendar.readonly"
EVENTS = _SCOPE + "calendar.events"
EVENTS_READONLY = _SCOPE + "calendar.events.readonly"
APP_CREATED = _SCOPE + "calendar.app.created"
FREEBUSY = _SCOPE + "calendar.freebusy"
CALENDARLIST = _SCOPE + "calendar.calendarlist"
CALENDARLIST_READONLY = _SCOPE + "calendar.calendarlist.readonly"
CALENDARS = _SCOPE + "calendar.calendars"
CALENDARS_READONLY = _SCOPE + "calendar.calendars.readonly"

# Which grants reach which call. APP_CREATED is handled apart: it reaches
# any of these on a calendar the app made and none of them elsewhere.
READ_CALENDAR_LIST = frozenset({CALENDAR, READONLY, CALENDARLIST, CALENDARLIST_READONLY})
READ_CALENDAR = frozenset({CALENDAR, READONLY, CALENDARS, CALENDARS_READONLY})
WRITE_CALENDAR = frozenset({CALENDAR, CALENDARS})
READ_EVENTS = frozenset({CALENDAR, READONLY, EVENTS, EVENTS_READONLY})
WRITE_EVENTS = frozenset({CALENDAR, EVENTS})
QUERY_FREEBUSY = frozenset({CALENDAR, READONLY, EVENTS, EVENTS_READONLY, FREEBUSY})

DEFAULT_PAGE_SIZE = 250
MAX_PAGE_SIZE = 2500
# How far a series without an end is expanded. Past this an absent instance
# says nothing, which is the bound the backend documents against.
EXPANSION_HORIZON = timedelta(days=800)
MAX_INSTANCES = 1000


@dataclass
class Calendar:
    id: str
    summary: str
    primary: bool = False
    app_created: bool = False
    events: dict[str, dict[str, Any]] = field(default_factory=dict)
    #: Bumped on every change; every event carries the value it last changed at.
    version: int = 0
    #: Bumped to age out every sync token handed out so far.
    epoch: int = 0

    def entry(self) -> dict[str, Any]:
        item: dict[str, Any] = {
            "kind": "calendar#calendarListEntry",
            "id": self.id,
            "summary": self.summary,
            "timeZone": ACCOUNT_TIME_ZONE,
            "accessRole": "owner",
        }
        if self.primary:
            item["primary"] = True
        return item

    def resource(self) -> dict[str, Any]:
        return {
            "kind": "calendar#calendar",
            "id": self.id,
            "summary": self.summary,
            "timeZone": ACCOUNT_TIME_ZONE,
        }

    def sync_token(self) -> str:
        return f"{self.id}|{self.epoch}|{self.version}"


@dataclass
class State:
    account: str = DEFAULT_ACCOUNT
    calendars: dict[str, Calendar] = field(default_factory=dict)
    #: The scopes the account has granted this client, all together.
    granted: set[str] = field(default_factory=set)
    codes: dict[str, dict[str, Any]] = field(default_factory=dict)
    tokens: dict[str, frozenset[str]] = field(default_factory=dict)
    refresh_tokens: dict[str, frozenset[str]] = field(default_factory=dict)
    #: Every request the backend made below the Google paths, in order.
    requests: list[dict[str, Any]] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.calendars[self.account] = Calendar(id=self.account, summary=self.account, primary=True)

    @property
    def primary(self) -> Calendar:
        return next(c for c in self.calendars.values() if c.primary)

    def calendar(self, calendar_id: str) -> Calendar | None:
        if calendar_id == "primary":
            return self.primary
        return self.calendars.get(calendar_id)


state = State()
app = FastAPI(title="fake-google")


# --- Google's error shape ------------------------------------------------


class GoogleError(Exception):
    def __init__(self, status: int, reason: str, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.reason = reason
        self.message = message


@app.exception_handler(GoogleError)
async def _google_error(_request: Request, exc: GoogleError) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status,
        content={
            "error": {
                "code": exc.status,
                "message": exc.message,
                "errors": [{"domain": "global", "reason": exc.reason, "message": exc.message}],
            }
        },
    )


def _not_found(what: str) -> GoogleError:
    return GoogleError(404, "notFound", f"{what} not found")


def _insufficient() -> GoogleError:
    return GoogleError(403, "insufficientPermissions", "Insufficient Permission")


@app.middleware("http")
async def _record(request: Request, call_next: Callable[[Request], Any]) -> Response:
    response: Response = await call_next(request)
    if not request.url.path.startswith("/_fake/"):
        state.requests.append(
            {"method": request.method, "path": request.url.path, "status": response.status_code}
        )
    return response


# --- OAuth -----------------------------------------------------------------


@app.get("/o/oauth2/auth")
async def authorize(request: Request) -> Response:
    """Grant what was asked for and send the browser straight back.

    Google shows a consent screen here; the stand-in behaves as though it
    was accepted as asked. What it asked for is what gets granted, so a
    request for a wider scope than the backend meant shows up in the grant.
    """
    params = request.query_params
    if params.get("client_id") != CLIENT_ID:
        return JSONResponse({"error": "invalid_client"}, status_code=400)
    redirect_uri = params.get("redirect_uri")
    if not redirect_uri:
        return JSONResponse({"error": "invalid_request"}, status_code=400)
    scopes = set((params.get("scope") or "").split())
    code = secrets.token_urlsafe(24)
    state.codes[code] = {
        "scopes": scopes,
        "redirect_uri": redirect_uri,
        "challenge": params.get("code_challenge"),
        "method": params.get("code_challenge_method", "plain"),
        "include_granted": params.get("include_granted_scopes") == "true",
    }
    joiner = "&" if "?" in redirect_uri else "?"
    back = {"code": code, "scope": " ".join(sorted(scopes))}
    if params.get("state") is not None:
        back["state"] = params["state"]
    return RedirectResponse(f"{redirect_uri}{joiner}{urlencode(back)}", status_code=302)


def _client_from(request: Request, form: dict[str, str]) -> tuple[str | None, str | None]:
    """The client id and secret, from the body or HTTP Basic — either is Google's."""
    header = request.headers.get("authorization", "")
    if header.startswith("Basic "):
        try:
            decoded = base64.b64decode(header[6:]).decode()
        except ValueError:
            return None, None
        client_id, _, secret = decoded.partition(":")
        return client_id, secret
    return form.get("client_id"), form.get("client_secret")


def _issue(scopes: set[str]) -> dict[str, Any]:
    granted = frozenset(scopes)
    access = "ya29.fake-" + secrets.token_urlsafe(16)
    refresh = "1//fake-" + secrets.token_urlsafe(16)
    state.tokens[access] = granted
    state.refresh_tokens[refresh] = granted
    return {
        "access_token": access,
        "expires_in": 3599,
        "refresh_token": refresh,
        "scope": " ".join(sorted(granted)),
        "token_type": "Bearer",
    }


class OAuthError(Exception):
    def __init__(self, error: str, description: str | None = None) -> None:
        super().__init__(error)
        self.error = error
        self.description = description


@app.exception_handler(OAuthError)
async def _oauth_error(_request: Request, exc: OAuthError) -> JSONResponse:
    body = {"error": exc.error}
    if exc.description:
        body["error_description"] = exc.description
    return JSONResponse(body, status_code=401 if exc.error == "invalid_client" else 400)


def _verify_pkce(pending: dict[str, Any], verifier: str | None) -> None:
    """The verifier must be the one the challenge was derived from."""
    if not pending["challenge"]:
        return
    if not verifier:
        raise OAuthError("invalid_grant", "Missing code verifier.")
    digest = hashlib.sha256(verifier.encode()).digest()
    expected = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
    if pending["method"] == "S256" and expected != pending["challenge"]:
        raise OAuthError("invalid_grant", "Bad code verifier.")


@app.post("/token")
async def token(request: Request) -> Response:
    form = {k: str(v) for k, v in (await request.form()).items()}
    client_id, secret = _client_from(request, form)
    if client_id != CLIENT_ID or secret != CLIENT_SECRET:
        raise OAuthError("invalid_client")

    if form.get("grant_type") == "refresh_token":
        scopes = state.refresh_tokens.get(form.get("refresh_token", ""))
        if scopes is None:
            raise OAuthError("invalid_grant")
        issued = _issue(set(scopes))
        del issued["refresh_token"]
        return JSONResponse(issued)

    pending = state.codes.pop(form.get("code", ""), None)
    if pending is None:
        raise OAuthError("invalid_grant")
    _verify_pkce(pending, form.get("code_verifier"))
    scopes = set(pending["scopes"])
    if pending["include_granted"]:
        scopes |= state.granted
    state.granted = set(scopes)
    return JSONResponse(_issue(scopes))


# --- Scopes ----------------------------------------------------------------


def _scopes_of(request: Request) -> frozenset[str]:
    header = request.headers.get("authorization", "")
    if not header.startswith("Bearer "):
        raise GoogleError(401, "authError", "Invalid Credentials")
    scopes = state.tokens.get(header[7:])
    if scopes is None:
        raise GoogleError(401, "authError", "Invalid Credentials")
    return scopes


def _allow(scopes: frozenset[str], allowed: frozenset[str], calendar: Calendar | None) -> None:
    """Refuse a call the token's grant does not reach."""
    if scopes & allowed:
        return
    if APP_CREATED in scopes and calendar is not None and calendar.app_created:
        return
    raise _insufficient()


def _calendar_or_404(calendar_id: str) -> Calendar:
    calendar = state.calendar(calendar_id)
    if calendar is None:
        raise _not_found("Calendar")
    return calendar


# --- Events ----------------------------------------------------------------


def _parse_time(slot: dict[str, Any]) -> datetime | None:
    """An event boundary as an instant; None for an all-day date.

    Google takes a ``dateTime`` with an offset, or without one when the
    slot names a ``timeZone``. Whatever comes in, what goes out carries an
    offset, because that is what Google always answers with.
    """
    raw = slot.get("dateTime")
    if not raw:
        return None
    parsed = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    zone = slot.get("timeZone")
    if parsed.tzinfo is None:
        if not zone:
            raise GoogleError(400, "badRequest", "Missing time zone definition for start time.")
        parsed = parsed.replace(tzinfo=ZoneInfo(zone))
    elif zone:
        parsed = parsed.astimezone(ZoneInfo(zone))
    return parsed


def _slot(instant: datetime, zone: str | None) -> dict[str, Any]:
    slot: dict[str, Any] = {"dateTime": instant.isoformat()}
    if zone:
        slot["timeZone"] = zone
    return slot


def _normalize(
    body: dict[str, Any], calendar: Calendar, *, existing: dict[str, Any] | None = None
) -> dict[str, Any]:
    """An event as Google would store it, stamped with this change."""
    event = dict(existing or {})
    event.update(body)
    event.setdefault("id", secrets.token_hex(13))
    event.setdefault("status", "confirmed")
    event.setdefault("kind", "calendar#event")
    for key in ("start", "end"):
        slot = event.get(key) or {}
        instant = _parse_time(slot)
        if instant is not None:
            event[key] = _slot(instant, slot.get("timeZone"))
    now = datetime.now(UTC).isoformat()
    event.setdefault("created", now)
    event["updated"] = now
    calendar.version += 1
    event["_version"] = calendar.version
    event["etag"] = f'"{calendar.version}"'
    return event


def _public(event: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in event.items() if not k.startswith("_")}


def _instance_id(master_id: str, start: datetime) -> str:
    return f"{master_id}_{start.astimezone(UTC):%Y%m%dT%H%M%SZ}"


def _occurrences(master: dict[str, Any]) -> Iterator[datetime]:
    start = _parse_time(master["start"])
    if start is None:
        return iter(())
    rules = [line for line in master.get("recurrence", []) if line.upper().startswith("RRULE")]
    if not rules:
        return iter(())
    rule = rrulestr(rules[0], dtstart=start)
    horizon = start + EXPANSION_HORIZON
    return (dt for dt in islice(rule, MAX_INSTANCES) if dt <= horizon)


def _expand(calendar: Calendar) -> list[dict[str, Any]]:
    """Every single event and every instance of every series, as ``singleEvents`` answers.

    An instance a spec changed on its own is stored under its instance id
    with ``recurringEventId``, as Google stores an exception, and stands in
    for the generated one.
    """
    exceptions = {
        event["id"]: event for event in calendar.events.values() if event.get("recurringEventId")
    }
    items: list[dict[str, Any]] = []
    for event in calendar.events.values():
        if event.get("recurringEventId"):
            items.append(event)
            continue
        if not event.get("recurrence"):
            items.append(event)
            continue
        start = _parse_time(event["start"])
        end = _parse_time(event["end"])
        if start is None or end is None:
            continue
        duration = end - start
        zone = event["start"].get("timeZone")
        for occurrence in _occurrences(event):
            instance_id = _instance_id(event["id"], occurrence)
            if instance_id in exceptions:
                continue
            instance = {k: v for k, v in event.items() if k != "recurrence"}
            instance.update(
                {
                    "id": instance_id,
                    "recurringEventId": event["id"],
                    "originalStartTime": _slot(occurrence, zone),
                    "start": _slot(occurrence, zone),
                    "end": _slot(occurrence + duration, zone),
                }
            )
            items.append(instance)
    return items


def _find(calendar: Calendar, event_id: str) -> dict[str, Any] | None:
    stored = calendar.events.get(event_id)
    if stored is not None:
        return stored
    master_id, _, _ = event_id.rpartition("_")
    if master_id and master_id in calendar.events:
        return next((e for e in _expand(calendar) if e["id"] == event_id), None)
    return None


def _time_bounds(params: Any) -> tuple[datetime | None, datetime | None]:
    def bound(name: str) -> datetime | None:
        raw = params.get(name)
        if not raw:
            return None
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)

    return bound("timeMin"), bound("timeMax")


def _overlaps(event: dict[str, Any], lower: datetime | None, upper: datetime | None) -> bool:
    start = _parse_time(event.get("start") or {})
    end = _parse_time(event.get("end") or {})
    if start is None or end is None:
        return lower is None and upper is None
    return (lower is None or end > lower) and (upper is None or start < upper)


def _sort_key(event: dict[str, Any]) -> tuple[str, str]:
    return (str((event.get("start") or {}).get("dateTime") or ""), str(event["id"]))


@app.get("/calendar/v3/calendars/{calendar_id}/events")
async def list_events(calendar_id: str, request: Request) -> Any:
    scopes = _scopes_of(request)
    calendar = _calendar_or_404(calendar_id)
    _allow(scopes, READ_EVENTS, calendar)
    params = request.query_params
    single = params.get("singleEvents") == "true"
    if params.get("orderBy") == "startTime" and not single:
        raise GoogleError(400, "badRequest", "orderBy startTime requires singleEvents")

    items = _expand(calendar) if single else list(calendar.events.values())
    sync_token = params.get("syncToken")
    if sync_token:
        if any(params.get(name) for name in ("timeMin", "timeMax", "orderBy")):
            raise GoogleError(
                400, "badRequest", "syncToken cannot be used with timeMin, timeMax or orderBy"
            )
        owner, _, rest = sync_token.partition("|")
        epoch, _, version = rest.partition("|")
        if owner != calendar.id or not epoch.isdigit() or not version.isdigit():
            raise GoogleError(400, "badRequest", "Invalid sync token")
        if int(epoch) != calendar.epoch:
            raise GoogleError(
                410, "fullSyncRequired", "Sync token is no longer valid, a full sync is required."
            )
        since = int(version)
        items = [event for event in items if event["_version"] > since]
    else:
        lower, upper = _time_bounds(params)
        if params.get("showDeleted") != "true":
            items = [event for event in items if event.get("status") != "cancelled"]
        items = [event for event in items if _overlaps(event, lower, upper)]

    items.sort(key=_sort_key)
    page_size = min(int(params.get("maxResults") or DEFAULT_PAGE_SIZE), MAX_PAGE_SIZE)
    offset = int(params.get("pageToken") or 0)
    page = items[offset : offset + page_size]
    response: dict[str, Any] = {
        "kind": "calendar#events",
        "summary": calendar.summary,
        "timeZone": ACCOUNT_TIME_ZONE,
        "accessRole": "owner",
        "items": [_public(event) for event in page],
    }
    if offset + page_size < len(items):
        response["nextPageToken"] = str(offset + page_size)
    else:
        response["nextSyncToken"] = calendar.sync_token()
    return response


@app.get("/calendar/v3/calendars/{calendar_id}/events/{event_id}")
async def get_event(calendar_id: str, event_id: str, request: Request) -> Any:
    scopes = _scopes_of(request)
    calendar = _calendar_or_404(calendar_id)
    _allow(scopes, READ_EVENTS, calendar)
    event = _find(calendar, event_id)
    if event is None:
        raise _not_found("Event")
    return _public(event)


@app.post("/calendar/v3/calendars/{calendar_id}/events")
async def insert_event(calendar_id: str, request: Request) -> Any:
    scopes = _scopes_of(request)
    calendar = _calendar_or_404(calendar_id)
    _allow(scopes, WRITE_EVENTS, calendar)
    event = _normalize(await request.json(), calendar)
    calendar.events[event["id"]] = event
    return _public(event)


def _change(
    calendar: Calendar, event_id: str, body: dict[str, Any], *, replace: bool
) -> dict[str, Any]:
    """Apply a change to a stored event, or to one instance of a series.

    Changing an instance that was only ever generated makes it an exception:
    stored on its own, under its instance id, from then on.
    """
    current = _find(calendar, event_id)
    if current is None:
        raise _not_found("Event")
    base = {"id": current["id"]}
    for key in ("recurringEventId", "originalStartTime", "created"):
        if key in current:
            base[key] = current[key]
    event = _normalize(body, calendar, existing=base if replace else current)
    calendar.events[event["id"]] = event
    return event


@app.put("/calendar/v3/calendars/{calendar_id}/events/{event_id}")
async def update_event(calendar_id: str, event_id: str, request: Request) -> Any:
    scopes = _scopes_of(request)
    calendar = _calendar_or_404(calendar_id)
    _allow(scopes, WRITE_EVENTS, calendar)
    return _public(_change(calendar, event_id, await request.json(), replace=True))


@app.patch("/calendar/v3/calendars/{calendar_id}/events/{event_id}")
async def patch_event(calendar_id: str, event_id: str, request: Request) -> Any:
    scopes = _scopes_of(request)
    calendar = _calendar_or_404(calendar_id)
    _allow(scopes, WRITE_EVENTS, calendar)
    return _public(_change(calendar, event_id, await request.json(), replace=False))


@app.delete("/calendar/v3/calendars/{calendar_id}/events/{event_id}")
async def delete_event(calendar_id: str, event_id: str, request: Request) -> Response:
    scopes = _scopes_of(request)
    calendar = _calendar_or_404(calendar_id)
    _allow(scopes, WRITE_EVENTS, calendar)
    _change(calendar, event_id, {"status": "cancelled"}, replace=False)
    return Response(status_code=204)


# --- Calendars -------------------------------------------------------------


@app.get("/calendar/v3/users/me/calendarList")
async def list_calendars(request: Request) -> Any:
    _allow(_scopes_of(request), READ_CALENDAR_LIST, None)
    return {
        "kind": "calendar#calendarList",
        "items": [c.entry() for c in state.calendars.values()],
    }


@app.get("/calendar/v3/users/me/calendarList/{calendar_id}")
async def get_calendar_entry(calendar_id: str, request: Request) -> Any:
    _allow(_scopes_of(request), READ_CALENDAR_LIST, None)
    return _calendar_or_404(calendar_id).entry()


@app.get("/calendar/v3/calendars/{calendar_id}")
async def get_calendar(calendar_id: str, request: Request) -> Any:
    scopes = _scopes_of(request)
    calendar = _calendar_or_404(calendar_id)
    _allow(scopes, READ_CALENDAR, calendar)
    return calendar.resource()


@app.post("/calendar/v3/calendars")
async def insert_calendar(request: Request) -> Any:
    scopes = _scopes_of(request)
    if not scopes & WRITE_CALENDAR and APP_CREATED not in scopes:
        raise _insufficient()
    body = await request.json()
    calendar = Calendar(
        id=f"{secrets.token_hex(13)}@group.calendar.google.com",
        summary=str(body.get("summary") or ""),
        app_created=True,
    )
    state.calendars[calendar.id] = calendar
    return calendar.resource()


@app.post("/calendar/v3/freeBusy")
async def free_busy(request: Request) -> Any:
    _allow(_scopes_of(request), QUERY_FREEBUSY, None)
    body = await request.json()
    lower, upper = _time_bounds(body)
    calendars: dict[str, Any] = {}
    for item in body.get("items", []):
        asked = str(item.get("id"))
        calendar = state.calendar(asked)
        if calendar is None:
            calendars[asked] = {"errors": [{"domain": "global", "reason": "notFound"}], "busy": []}
            continue
        busy = [
            {"start": event["start"]["dateTime"], "end": event["end"]["dateTime"]}
            for event in sorted(_expand(calendar), key=_sort_key)
            if event.get("status") != "cancelled"
            and event.get("start", {}).get("dateTime")
            and _overlaps(event, lower, upper)
        ]
        calendars[asked] = {"busy": busy}
    return {
        "kind": "calendar#freeBusy",
        "timeMin": body.get("timeMin"),
        "timeMax": body.get("timeMax"),
        "calendars": calendars,
    }


# --- The spec's side -------------------------------------------------------


@app.get("/_fake/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/_fake/reset")
async def reset(request: Request) -> dict[str, str]:
    """Start over: one account, its main calendar, nothing granted.

    ``account`` names the account, and so the main calendar's id; a spec
    that picks a fresh one leaves nothing the backend remembers about the
    last one pointing at this calendar.
    """
    global state  # noqa: PLW0603 — the whole point of a reset
    body = await request.json() if int(request.headers.get("content-length") or 0) else {}
    state = State(account=str(body.get("account") or DEFAULT_ACCOUNT))
    return {"account": state.account}


@app.get("/_fake/grant")
async def grant() -> dict[str, list[str]]:
    """What the account has granted this client so far."""
    return {"scopes": sorted(state.granted)}


@app.get("/_fake/requests")
async def requests_made() -> dict[str, list[dict[str, Any]]]:
    return {"requests": list(state.requests)}


@app.get("/_fake/calendars")
async def fake_calendars() -> dict[str, Any]:
    return {"calendars": [c.entry() for c in state.calendars.values()]}


@app.post("/_fake/calendars")
async def fake_add_calendar(request: Request) -> Any:
    """A second calendar on the account, as one shared with it or made by hand."""
    body = await request.json()
    calendar = Calendar(
        id=str(body.get("id") or f"{secrets.token_hex(8)}@group.calendar.google.com"),
        summary=str(body.get("summary") or "Calendar"),
    )
    state.calendars[calendar.id] = calendar
    return calendar.entry()


@app.get("/_fake/calendars/{calendar_id}/events")
async def fake_events(calendar_id: str) -> dict[str, Any]:
    """What is on the calendar as stored: series as one event, plus exceptions."""
    calendar = _calendar_or_404(calendar_id)
    return {"events": [_public(event) for event in calendar.events.values()]}


@app.post("/_fake/calendars/{calendar_id}/events")
async def fake_seed_event(calendar_id: str, request: Request) -> Any:
    """Put an event on the calendar, in Google's own event shape."""
    calendar = _calendar_or_404(calendar_id)
    event = _normalize(await request.json(), calendar)
    calendar.events[event["id"]] = event
    return _public(event)


@app.patch("/_fake/calendars/{calendar_id}/events/{event_id}")
async def fake_change_event(calendar_id: str, event_id: str, request: Request) -> Any:
    """Change an event or a series, or one instance of a series, as its owner would."""
    calendar = _calendar_or_404(calendar_id)
    return _public(_change(calendar, event_id, await request.json(), replace=False))


@app.delete("/_fake/calendars/{calendar_id}/events/{event_id}")
async def fake_delete_event(calendar_id: str, event_id: str) -> Response:
    calendar = _calendar_or_404(calendar_id)
    _change(calendar, event_id, {"status": "cancelled"}, replace=False)
    return Response(status_code=204)


@app.post("/_fake/calendars/{calendar_id}/expire-sync-tokens")
async def fake_expire_sync_tokens(calendar_id: str) -> dict[str, int]:
    """Age out every sync token for this calendar: the next resumed read gets 410."""
    calendar = _calendar_or_404(calendar_id)
    calendar.epoch += 1
    return {"epoch": calendar.epoch}
