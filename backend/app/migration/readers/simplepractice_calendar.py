# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Read a saved SimplePractice web-calendar feed into typed records.

The SimplePractice web calendar loads its appointments as JSON from
``GET /frontend/appointments`` (JSON:API, the clients included). A practice
that keeps SimplePractice for scheduling can save those responses as files
under a ``calendar/`` folder and import the folder: the export archive
carries notes, cards and documents, never the calendar ahead. The shape this
module parses is the one captured in
``backend/tests/fixtures/simplepractice_calendar`` — read that folder's
README before changing anything here.

Facts about the feed every caller has to keep in mind:

* **Booleans are the strings ``"true"`` and ``"false"``.** They are read as
  ``bool`` here and nowhere else.
* **Times carry the practice's own offset**, which differs on the two sides
  of a daylight-time change. They are kept as aware datetimes with that
  offset; nothing here converts them.
* **Files overlap.** One file covers whatever window the calendar view
  asked for, and the same appointment id appears in several files when the
  practice pages back and forth. File names sort in capture order, so the
  copy in the latest file is the one that counts.
* **A client's name arrives in one of two places.** A fuller feed includes
  the client with ``firstName`` / ``lastName`` / ``preferredName``; a
  leaner one includes the client with ``isMinor`` alone and puts the
  client's display name in the appointment's ``title``. The reader takes
  the name from the client entry when it is there and from the title of a
  one-client appointment otherwise, and records which.
* **Group and outside events ride along.** ``groupAppointments`` and
  ``externalAppointments`` entries share the ``data`` list. Neither is read
  into a record: the first is reported as unsupported, the second counted
  as skipped.

Everything here is pure: a directory in, dataclasses out, nothing else
touched.
"""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from .simplepractice import SOURCE_SYSTEM

#: The folder the saved responses live in, at the top of the uploaded zip.
CALENDAR_DIR = "calendar"

#: The ``attendanceStatus`` of a visit that happened.
ATTENDED = "Show"
#: The ledger's record type for a client landed from a calendar feed. Kept
#: apart from the export's ``contact`` so a later export import does not
#: mistake a name-only patient for one whose card already landed.
CALENDAR_CLIENT = "calendar_client"

#: A display name of first and last name has this many words.
_FIRST_AND_LAST = 2


# --------------------------------------------------------------------------- records


@dataclass(frozen=True)
class CalendarClient:
    """One client the feed includes. ``source_id`` is SimplePractice's client
    id, the same id the export archive writes in a contact card's file name."""

    source_id: str
    first_name: str
    last_name: str
    middle_name: str | None
    preferred_name: str | None
    hashed_id: str | None
    is_minor: bool
    #: ``enhancedDataProtection`` was set, or an administrator's access was
    #: revoked: the practice treats this client's record with extra care.
    protected: bool
    #: ``client`` when the name came from the included client entry,
    #: ``title`` when it came from the appointment's title.
    name_from: str
    digest: str

    @property
    def display_name(self) -> str:
        return " ".join(f"{self.first_name} {self.last_name}".split())

    @property
    def preferred_full_name(self) -> str | None:
        """The preferred first name with the family name, when there is one."""
        if not self.preferred_name:
            return None
        return " ".join(f"{self.preferred_name} {self.last_name}".split())


@dataclass(frozen=True)
class CalendarAppointment:
    source_id: str
    client_id: str
    start_at: datetime
    end_at: datetime
    duration_minutes: int
    #: ``Show``, ``No Show``, ``Cancelled``, ``Late Cancelled``; ``None`` when
    #: the feed has not recorded attendance.
    attendance_status: str | None
    office_name: str | None
    full_day: bool
    is_recurring: bool
    has_note: bool
    #: The file this copy was read from — the latest file that carried the id.
    path: str
    digest: str

    @property
    def attended(self) -> bool:
        return self.attendance_status == ATTENDED


@dataclass(frozen=True)
class Unsupported:
    """A feed entry this version does not land: a group appointment, or a
    ``data`` entry of a type the reader does not know."""

    kind: str
    source_id: str
    path: str


@dataclass(frozen=True)
class SimplePracticeCalendar:
    root: str
    clients: tuple[CalendarClient, ...] = ()
    appointments: tuple[CalendarAppointment, ...] = ()
    unsupported: tuple[Unsupported, ...] = ()
    #: Counts by reason: ``external`` for the clinician's outside events,
    #: ``no_client`` for an appointment that names no client, ``client_missing``
    #: for one whose client the file did not include.
    skipped: dict[str, int] = field(default_factory=dict)
    #: The files read, in the order they were read.
    files: tuple[str, ...] = ()
    unreadable: tuple[tuple[str, str], ...] = ()


# --------------------------------------------------------------------------- helpers


def _flag(value: Any) -> bool:
    """The feed's ``"true"`` / ``"false"`` strings (and a real bool) as bool."""
    if isinstance(value, bool):
        return value
    return isinstance(value, str) and value.strip().lower() == "true"


def _when(value: Any) -> datetime:
    """An ISO-8601 stamp with its offset, kept as the feed wrote it."""
    if not isinstance(value, str):
        raise ValueError("a time is missing")
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        raise ValueError("a time carries no offset")
    return parsed


def _digest(payload: dict[str, Any]) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _revoked_admin_ids(value: Any) -> tuple[str, ...]:
    """``revokedAdminIds`` arrives as a JSON list serialised into a string."""
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            return ()
    if not isinstance(value, list):
        return ()
    return tuple(str(v) for v in value)


def _split_name(display_name: str) -> tuple[str, str]:
    """A display name as first and last name: everything before the last
    word, and the last word."""
    parts = display_name.split()
    if len(parts) < _FIRST_AND_LAST:
        return display_name.strip(), ""
    return " ".join(parts[:-1]), parts[-1]


# --------------------------------------------------------------------------- one file


@dataclass
class _File:
    """What one response holds, before files are merged."""

    path: str
    clients: dict[str, CalendarClient] = field(default_factory=dict)
    appointments: dict[str, CalendarAppointment] = field(default_factory=dict)
    unsupported: dict[tuple[str, str], Unsupported] = field(default_factory=dict)
    skipped: Counter[str] = field(default_factory=Counter)


def _client(entry: dict[str, Any], title: str | None) -> CalendarClient:
    attrs = entry.get("attributes") or {}
    first = (attrs.get("firstName") or "").strip()
    last = (attrs.get("lastName") or "").strip()
    name_from = "client"
    if not first and not last and title:
        first, last = _split_name(title)
        name_from = "title"
    protected = _flag(attrs.get("enhancedDataProtection")) or bool(
        _revoked_admin_ids(attrs.get("revokedAdminIds"))
    )
    source_id = str(entry["id"])
    return CalendarClient(
        source_id=source_id,
        first_name=first,
        last_name=last,
        middle_name=(attrs.get("middleName") or None),
        preferred_name=(attrs.get("preferredName") or None),
        hashed_id=(attrs.get("hashedId") or None),
        is_minor=_flag(attrs.get("isMinor")),
        protected=protected,
        name_from=name_from,
        digest=_digest(
            {
                "id": source_id,
                "firstName": first,
                "lastName": last,
                "middleName": attrs.get("middleName"),
                "preferredName": attrs.get("preferredName"),
                "hashedId": attrs.get("hashedId"),
                "protected": protected,
                "isMinor": _flag(attrs.get("isMinor")),
            }
        ),
    )


_Included = dict[tuple[str, str], dict[str, Any]]


def _client_id_of(entry: dict[str, Any], included: _Included) -> str | None:
    """The one client an appointment is for, through ``appointmentClients``."""
    links = ((entry.get("relationships") or {}).get("appointmentClients") or {}).get("data") or []
    client_ids: list[str] = []
    for link in links:
        joined = included.get(("appointmentClients", str(link.get("id"))))
        if joined is None:
            continue
        client = ((joined.get("relationships") or {}).get("client") or {}).get("data") or {}
        if client.get("id") is not None:
            client_ids.append(str(client["id"]))
    return client_ids[0] if len(client_ids) == 1 else None


def _appointment(entry: dict[str, Any], client_id: str, path: str) -> CalendarAppointment:
    attrs = entry.get("attributes") or {}
    start = _when(attrs.get("startTime"))
    end = _when(attrs.get("endTime"))
    duration = attrs.get("duration")
    minutes = int(duration) if duration is not None else int((end - start).total_seconds() // 60)
    status = attrs.get("attendanceStatus") or None
    source_id = str(entry["id"])
    # The digest covers what the import lands, so a permission or note flag
    # moving in the feed does not read as a change to the appointment.
    digest = _digest(
        {
            "id": source_id,
            "clientId": client_id,
            "startTime": start.isoformat(),
            "endTime": end.isoformat(),
            "duration": minutes,
            "attendanceStatus": status,
            "fullDay": _flag(attrs.get("fullDay")),
        }
    )
    return CalendarAppointment(
        source_id=source_id,
        client_id=client_id,
        start_at=start,
        end_at=end,
        duration_minutes=minutes,
        attendance_status=status,
        office_name=(attrs.get("officeName") or None),
        full_day=_flag(attrs.get("fullDay")),
        is_recurring=_flag(attrs.get("isRecurring")),
        has_note=_flag(attrs.get("hasNote")),
        path=path,
        digest=digest,
    )


def _read_file(path: Path, rel: str) -> _File:
    document = json.loads(path.read_bytes())
    if not isinstance(document, dict) or not isinstance(document.get("data"), list):
        raise ValueError("not a calendar response")
    included: _Included = {
        (str(e.get("type")), str(e.get("id"))): e
        for e in document.get("included") or []
        if isinstance(e, dict)
    }
    out = _File(path=rel)
    for entry in document["data"]:
        if not isinstance(entry, dict) or entry.get("id") is None:
            continue
        kind = entry.get("type")
        if kind == "externalAppointments":
            out.skipped["external"] += 1
            continue
        if kind != "appointments":
            name = "group" if kind == "groupAppointments" else str(kind)
            out.unsupported[(name, str(entry["id"]))] = Unsupported(name, str(entry["id"]), rel)
            continue
        try:
            client_id = _client_id_of(entry, included)
            if client_id is None:
                out.skipped["no_client"] += 1
                continue
            client_entry = included.get(("clients", client_id))
            if client_entry is None:
                out.skipped["client_missing"] += 1
                continue
            title = (str((entry.get("attributes") or {}).get("title") or "")).strip() or None
            client = _client(client_entry, title)
            appointment = _appointment(entry, client_id, rel)
        except (ValueError, TypeError, AttributeError, KeyError):
            # One appointment the file recorded incompletely (no end time, a
            # field of the wrong shape) is counted and left out; the rest of
            # the file still reads.
            out.skipped["malformed"] += 1
            continue
        out.clients[client_id] = _better_client(out.clients.get(client_id), client)
        out.appointments[appointment.source_id] = appointment
    return out


def _better_client(current: CalendarClient | None, new: CalendarClient) -> CalendarClient:
    """The copy of a client to keep when two reads disagree.

    The later copy wins, as everywhere in the feed, except that a name taken
    from an appointment's title never replaces one the client entry itself
    gave: a title is display text and may be shortened.
    """
    if current is not None and current.name_from == "client" and new.name_from == "title":
        return current
    return new


# --------------------------------------------------------------------------- the folder


def read_simplepractice_calendar(root: Path) -> SimplePracticeCalendar:
    """Read every ``calendar/*.json`` under ``root`` and merge them.

    Files are read in name order, which is capture order, and a later file's
    copy of an appointment or a client replaces an earlier one. A file that is
    not a calendar response is listed under ``unreadable`` with the reason and
    does not stop the read.
    """
    root = Path(root)
    clients: dict[str, CalendarClient] = {}
    appointments: dict[str, CalendarAppointment] = {}
    unsupported: dict[tuple[str, str], Unsupported] = {}
    skipped: Counter[str] = Counter()
    files: list[str] = []
    unreadable: list[tuple[str, str]] = []
    for path in sorted((root / CALENDAR_DIR).glob("*.json")):
        if not path.is_file():
            continue
        rel = path.relative_to(root).as_posix()
        files.append(rel)
        try:
            read = _read_file(path, rel)
        except (ValueError, OSError, TypeError, AttributeError) as exc:
            unreadable.append(
                (rel, type(exc).__name__ if not isinstance(exc, ValueError) else str(exc))
            )
            continue
        for client_id, client in read.clients.items():
            clients[client_id] = _better_client(clients.get(client_id), client)
        appointments.update(read.appointments)
        unsupported.update(read.unsupported)
        skipped.update(read.skipped)
    return SimplePracticeCalendar(
        root=str(root),
        clients=tuple(clients[k] for k in sorted(clients)),
        appointments=tuple(appointments[k] for k in sorted(appointments)),
        unsupported=tuple(unsupported[k] for k in sorted(unsupported)),
        skipped=dict(skipped),
        files=tuple(files),
        unreadable=tuple(unreadable),
    )


__all__ = [
    "ATTENDED",
    "CALENDAR_CLIENT",
    "CALENDAR_DIR",
    "SOURCE_SYSTEM",
    "CalendarAppointment",
    "CalendarClient",
    "SimplePracticeCalendar",
    "Unsupported",
    "read_simplepractice_calendar",
]
