# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""iCal calendar feed sync service for EHR integration.

Fetches iCal feeds from SimplePractice and Sessions Health, parses events,
and syncs them into Pablo's appointment system with client matching.

HIPAA Compliance:
- Feed URLs encrypted at rest (AES-256-GCM) — they are bearer-token-equivalent
- No PHI (client names, feed URLs, response bodies) in log messages
- Appointment titles stored as generic "Session" (not raw SUMMARY)

An event whose client can't be matched is not an appointment — an appointment
always has a patient. It is held as an open outside session
(``outside_sessions``) and asked about; the answer is remembered, and the
feed's later events for that client are booked on their own when the title
names one client. A title that can't — initials, or a name two charts share —
is asked about every time, pre-filled. ``OutsideSessions.unattended`` is the
one rule for that, shared with the calendars Pablo follows.

What a SimplePractice feed carries (a real feed, 2026-09-30): UID, start and
end, SUMMARY, an empty LOCATION, a telehealth URL per appointment, and
DTSTAMP. No RRULE: a weekly series arrives as one VEVENT per occurrence,
months ahead. The UID is the appointment number, so it follows when the
appointment was booked, not the client. The calendar-sync setting changes
SUMMARY only: initials ("J.A. Appointment") or a full name ("John Adams
Appointment", a middle initial dropped, typed as typed). A cancelled
appointment simply leaves the feed.
"""

from __future__ import annotations

import csv
import io
import logging
import re
import uuid
import zipfile
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, ClassVar, Literal
from urllib.parse import unquote, urlparse
from urllib.request import Request, urlopen

from icalendar import Calendar

from ..calendar_providers.source_identity import ical_source
from ..models.enums import EhrSystem
from ..patients.matching import (
    MatchContext,
    MatchResult,
    PatientHint,
    match_patient,
    remember_match,
)
from ..repositories.external_calendar_event import ANSWER_OPEN, ExternalCalendarEvent
from ..repositories.ical_sync_config import ICalSyncConfig, ICalSyncConfigRepository
from ..scheduling_engine.models.appointment import Appointment, AppointmentStatus
from ..settings import get_settings
from ..utcnow import utc_now
from .outside_sessions import OutsideSessions
from .token_encryption import decrypt_tokens, encrypt_tokens

if TYPE_CHECKING:
    from collections.abc import Iterable

    from ..repositories.external_calendar_event import ExternalCalendarEventRepository
    from ..repositories.patient import PatientRepository
    from ..repositories.patient_source_mapping import PatientSourceMappingRepository
    from ..scheduling_engine.repositories.appointment import AppointmentRepository

logger = logging.getLogger(__name__)

FETCH_TIMEOUT_SECONDS = 30
SP_APPOINTMENT_URL = "https://secure.simplepractice.com/appointments/{uid}"

#: What the settings card says when a stored feed URL fails the allowlist.
FEED_URL_REFUSED = "This feed's address isn't accepted. Disconnect it and connect again."

# SimplePractice SUMMARY patterns. Initials are two or more letters each
# followed by a full stop ("J.A.", "J.Q.A."); anything else before
# " Appointment" is a name as typed.
_SP_INITIALS_RE = re.compile(r"^((?:[A-Z]\.){2,})\s+Appointment$")
_SP_FULLNAME_RE = re.compile(r"^(.+?)\s+Appointment$")

#: What a feed's title says about the client it names.
#:
#: * ``initials``: initials only. Never identify anyone: every event is asked.
#: * ``name``: a full name. Identifies a client when exactly one of the
#:   clinician's charts bears it.
#: * ``code``: the feed's own client identifier (Sessions Health's
#:   ``SH00001``), which is one client's for good once answered.
FeedTitleKind = Literal["initials", "name", "code"]

#: How a feed names clients across a whole read, for the settings screen.
TitleStyle = Literal["initials", "names", "codes"]

# Sessions Health client code pattern
_SH_CODE_RE = re.compile(r"^SH(\d+)$")


def _now() -> datetime:
    return utc_now()


@dataclass
class ParsedEvent:
    """A parsed VEVENT from an iCal feed."""

    uid: str
    summary: str
    start_at: datetime
    end_at: datetime
    duration_minutes: int
    url: str | None = None  # video link (SP) or event link (SH)


@dataclass
class SyncResult:
    """Result of an iCal sync operation."""

    created: int = 0
    updated: int = 0
    deleted: int = 0
    unchanged: int = 0
    unmatched_events: list[dict[str, str]] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    title_style: TitleStyle | None = None
    """How the feed named clients in this read; None when it held no events."""


@dataclass
class ConfigureResult:
    """Result of configuring an iCal feed."""

    event_count: int = 0
    ehr_system: str = ""


@dataclass
class ConnectionStatus:
    """Status of a configured iCal feed."""

    ehr_system: str
    connected: bool
    last_synced_at: datetime | None = None
    last_sync_error: str | None = None
    title_style: str | None = None


@dataclass
class ImportResult:
    """Result of importing clients from a CSV/zip export."""

    imported: int = 0
    updated: int = 0
    skipped: int = 0
    mappings_created: int = 0
    errors: list[str] = field(default_factory=list)


class ICalSyncService:
    """Syncs appointments from EHR iCal feeds into Pablo."""

    def __init__(
        self,
        config_repo: ICalSyncConfigRepository,
        appointment_repo: AppointmentRepository,
        patient_repo: PatientRepository,
        mapping_repo: PatientSourceMappingRepository,
        external_events: ExternalCalendarEventRepository | None = None,
    ) -> None:
        self._config_repo = config_repo
        self._appt_repo = appointment_repo
        self._patient_repo = patient_repo
        self._mapping_repo = mapping_repo
        if external_events is None:
            # Callers that predate outside sessions construct this service
            # without it; they get the default repository rather than an error.
            from ..repositories import get_external_calendar_event_repository

            external_events = get_external_calendar_event_repository()
        self._outside = OutsideSessions(
            external_events, appointment_repo, patient_repo, mapping_repo
        )

    def configure(self, user_id: str, ehr_system: str, feed_url: str) -> ConfigureResult:
        """Validate, test-fetch, encrypt, and store the iCal feed URL."""
        safe_url = self._validate_feed_url(ehr_system, feed_url)

        # Test-fetch to verify the URL works
        ical_data = self._fetch_feed(safe_url)
        events = self._parse_events(ical_data)

        # The validated form is what is kept, so every later read fetches
        # exactly what the allowlist accepted, never the string as typed.
        encrypted = encrypt_tokens({"feed_url": safe_url})
        config = ICalSyncConfig(
            user_id=user_id,
            ehr_system=ehr_system,
            encrypted_feed_url=encrypted,
            connected_at=_now(),
        )
        self._config_repo.save(config)

        logger.info("iCal feed configured for user (events=%d)", len(events))
        return ConfigureResult(event_count=len(events), ehr_system=ehr_system)

    def disconnect(self, user_id: str, ehr_system: str) -> bool:
        """Remove stored feed URL. Does NOT delete synced appointments."""
        return self._config_repo.delete(user_id, ehr_system)

    def get_status(self, user_id: str) -> list[ConnectionStatus]:
        """Return connection status for all configured iCal sources."""
        configs = self._config_repo.list_by_user(user_id)
        return [
            ConnectionStatus(
                ehr_system=c.ehr_system,
                connected=True,
                last_synced_at=c.last_synced_at,
                last_sync_error=c.last_sync_error,
                title_style=c.title_style,
            )
            for c in configs
        ]

    def sync(self, user_id: str, ehr_system: str | None = None) -> list[SyncResult]:
        """Sync one or all configured iCal sources."""
        configs = self._config_repo.list_by_user(user_id)
        if ehr_system:
            configs = [c for c in configs if c.ehr_system == ehr_system]

        results = []
        for config in configs:
            try:
                result = self._sync_source(user_id, config)
                self._config_repo.update_sync_status(
                    user_id, config.ehr_system, error=None, title_style=result.title_style
                )
                results.append(result)
            except ValueError:
                # The stored URL no longer passes the allowlist (a row kept
                # as typed, before the validated form was what was stored).
                # Nothing was fetched; reconnecting stores it properly.
                logger.warning("iCal feed URL refused by the allowlist; not fetched")
                error_msg = FEED_URL_REFUSED
                self._config_repo.update_sync_status(user_id, config.ehr_system, error=error_msg)
                results.append(SyncResult(errors=[error_msg]))
            except Exception:
                logger.exception("iCal sync failed for source")
                error_msg = "Sync failed — could not fetch or parse feed"
                self._config_repo.update_sync_status(user_id, config.ehr_system, error=error_msg)
                error_result = SyncResult(errors=[error_msg])
                results.append(error_result)
        return results

    def resolve_client(
        self,
        user_id: str,
        ehr_system: str,
        client_identifier: str,
        patient_id: str,
    ) -> None:
        """Map a client identifier to a patient, booking the feed events waiting on it.

        An identifier that is asked about every time (initials, a shared
        name) can't be answered for all its events at once; those are
        answered one event each through the outside-sessions review.
        """
        self._outside.answer(
            user_id, ical_source(ehr_system), client_identifier, patient_id=patient_id
        )

    def import_clients(
        self,
        user_id: str,
        ehr_system: str,
        file_content: bytes,
        filename: str,
    ) -> ImportResult:
        """Import clients from a CSV or zip export and auto-create mappings."""
        csv_text = self._extract_csv(file_content, filename)
        if csv_text is None:
            return ImportResult(errors=["Could not find clients.csv in upload"])

        reader = csv.DictReader(io.StringIO(csv_text))
        result = ImportResult()

        all_patients, _ = self._patient_repo.list_by_user(user_id, page=1, page_size=10000)
        existing_by_name = {(p.first_name.lower(), p.last_name.lower()): p for p in all_patients}
        ctx = self._match_context(user_id)

        for row_num, row in enumerate(reader, start=1):
            first_name = row.get("First Name", "").strip()
            last_name = row.get("Last Name", "").strip()
            if not first_name or not last_name:
                result.errors.append(f"Row {row_num}: missing name")
                continue

            key = (first_name.lower(), last_name.lower())
            if key in existing_by_name:
                # Update existing patient with any newly populated fields
                if self._update_patient_from_csv(existing_by_name[key], row):
                    result.updated += 1
                else:
                    result.skipped += 1
            else:
                patient = self._create_patient_from_csv(row)
                self._patient_repo.create(patient, user_id)
                existing_by_name[key] = patient
                result.imported += 1

            # Auto-create SH code mapping for Sessions Health
            if ehr_system == EhrSystem.SESSIONS_HEALTH:
                sh_code = f"SH{row_num:05d}"
                patient = existing_by_name.get(key)
                patient_id = patient.id if patient else None
                if patient_id:
                    remember_match(ehr_system, sh_code, patient_id, ctx)
                    result.mappings_created += 1

        return result

    # --- Private helpers ---

    def _sync_source(self, user_id: str, config: ICalSyncConfig) -> SyncResult:
        """Sync a single iCal source."""
        tokens = decrypt_tokens(config.encrypted_feed_url)
        # Held to the allowlist on every read, not only when connected: a
        # row stored before the validated form was kept holds the URL as
        # typed, and a stored URL must not be a way past the check.
        feed_url = self._validate_feed_url(config.ehr_system, tokens["feed_url"])

        ical_data = self._fetch_feed(feed_url)
        feed_events = {e.uid: e for e in self._parse_events(ical_data)}

        existing_appts = self._appt_repo.list_by_ical_source(user_id, config.ehr_system)
        existing_by_uid = {a.ical_uid: a for a in existing_appts if a.ical_uid}

        # Patients and remembered identifiers load once for the whole feed
        ctx = self._match_context(user_id)

        result = SyncResult(title_style=_title_style(config.ehr_system, feed_events.values()))

        # Create or update
        for uid, event in feed_events.items():
            if uid in existing_by_uid:
                existing = existing_by_uid[uid]
                updated = self._update_if_changed(existing, event, config.ehr_system)

                # Re-attempt matching for previously unmatched appointments
                if not existing.patient_id:
                    client_id = self._extract_client_identifier(config.ehr_system, event.summary)
                    patient_id = self._outside.unattended(
                        self._row(user_id, config.ehr_system, event), ctx
                    )
                    if patient_id:
                        existing.patient_id = patient_id
                        existing.notes = f"ical_client:{client_id}"
                        existing.updated_at = _now()
                        self._appt_repo.update(existing)
                        updated = True

                if updated:
                    result.updated += 1
                else:
                    result.unchanged += 1
            else:
                self._add_event(user_id, config.ehr_system, event, ctx, result)

        self._outside.forget(user_id, ical_source(config.ehr_system), keep=set(feed_events))

        # Soft-delete events no longer in feed
        for uid, appt in existing_by_uid.items():
            if uid not in feed_events and appt.ical_sync_status != "deleted":
                appt.status = AppointmentStatus.CANCELLED
                appt.ical_sync_status = "deleted"
                appt.updated_at = _now()
                self._appt_repo.update(appt)
                result.deleted += 1

        logger.info(
            "iCal sync complete (created=%d, updated=%d, deleted=%d, unchanged=%d)",
            result.created,
            result.updated,
            result.deleted,
            result.unchanged,
        )
        return result

    def _add_event(
        self,
        user_id: str,
        ehr_system: str,
        event: ParsedEvent,
        ctx: MatchContext,
        result: SyncResult,
    ) -> None:
        """Book a feed event new to Pablo, or hold it until its client is known.

        Whether the title names one client — and so whether a match books
        without asking — is ``OutsideSessions.unattended``'s call, the same
        one made for a calendar Pablo follows. A client of the practice this
        clinician doesn't see is held as a question like any other unknown,
        never booked onto that chart.
        """
        source = ical_source(ehr_system)
        client_id = self._extract_client_identifier(ehr_system, event.summary)
        row = self._row(user_id, ehr_system, event)
        if self._outside.dismissed(row, ctx):
            # The clinician said this is not a client: skip it, don't ask.
            self._outside.drop(user_id, source, event.uid)
            return
        patient_id = self._outside.unattended(row, ctx)
        if patient_id:
            appt = self._create_appointment(user_id, ehr_system, event, patient_id)
            if client_id:
                appt.notes = f"ical_client:{client_id}"
            self._appt_repo.create(appt)
            self._outside.settle(user_id, source, event.uid, appt)
            result.created += 1
            return
        # No patient, so not an appointment: held until the clinician says
        # who it is.
        self._outside.hold(row)
        if row.answer != ANSWER_OPEN:
            # Already answered for this event alone (not a client): kept so
            # it isn't asked again, and not reported as unmatched either.
            return
        result.unmatched_events.append(
            {
                "ical_uid": event.uid,
                "client_identifier": client_id or "unknown",
                "start_at": event.start_at.isoformat(),
                "ehr_appointment_url": self._derive_appointment_url(
                    ehr_system, event.uid, event.url
                )
                or "",
            }
        )

    @staticmethod
    def _row(user_id: str, ehr_system: str, event: ParsedEvent) -> ExternalCalendarEvent:
        """The feed event as an outside session, for the booking rule and for holding."""
        return ExternalCalendarEvent(
            id=str(uuid.uuid4()),
            user_id=user_id,
            source=ical_source(ehr_system),
            source_event_id=event.uid,
            start_at=event.start_at,
            end_at=event.end_at,
            title=event.summary,
        )

    def _fetch_feed(self, feed_url: str) -> str:
        """Fetch iCal feed data via HTTP GET."""
        req = Request(_fetch_url(feed_url), headers={"User-Agent": "Pablo/1.0"})  # noqa: S310
        # feed_url pre-validated by _validate_feed_url() (https + host allowlist) — no SSRF.
        # The origin it is read from is the operator's setting, never the URL's.
        # nosemgrep
        with urlopen(req, timeout=FETCH_TIMEOUT_SECONDS) as resp:  # noqa: S310
            raw: bytes = resp.read()
            return raw.decode("utf-8")

    def _parse_events(self, ical_data: str) -> list[ParsedEvent]:
        """Parse iCal data into structured events."""
        cal = Calendar.from_ical(ical_data)
        events = []
        for component in cal.walk():
            if component.name != "VEVENT":
                continue

            uid = str(component.get("UID", ""))
            summary = str(component.get("SUMMARY", ""))

            dtstart = component.get("DTSTART")
            dtend = component.get("DTEND")
            if not dtstart or not dtend:
                continue

            start_dt = dtstart.dt
            end_dt = dtend.dt

            # Convert timezone-aware datetimes to UTC ISO 8601
            if hasattr(start_dt, "astimezone"):
                start_utc = start_dt.astimezone(UTC)
                end_utc = end_dt.astimezone(UTC)
            else:
                # All-day events (date objects) — skip
                continue

            duration = int((end_utc - start_utc).total_seconds() / 60)

            url_prop = component.get("URL")
            url = str(url_prop) if url_prop else None

            events.append(
                ParsedEvent(
                    uid=uid,
                    summary=summary,
                    start_at=start_utc,
                    end_at=end_utc,
                    duration_minutes=duration,
                    url=url,
                )
            )
        return events

    @staticmethod
    def _extract_client_identifier(ehr_system: str, summary: str) -> str:
        """Extract the client identifier from the SUMMARY field."""
        if ehr_system == EhrSystem.SIMPLEPRACTICE:
            # Try initials first: "J.A. Appointment"
            m = _SP_INITIALS_RE.match(summary)
            if m:
                return m.group(1)
            # Try full name: "Jane Adams Appointment"
            m = _SP_FULLNAME_RE.match(summary)
            if m:
                return m.group(1).strip()
            return summary
        if ehr_system == EhrSystem.SESSIONS_HEALTH:
            # "SH00001" — return as-is
            return summary.strip()
        return summary

    def _get_client_identifier(self, _ehr_system: str, notes: str) -> str:
        """Extract client identifier from appointment notes."""
        if notes.startswith("ical_client:"):
            return notes[len("ical_client:") :]
        return ""

    def _match_context(self, user_id: str) -> MatchContext:
        return MatchContext.for_practice(user_id, self._patient_repo, self._mapping_repo)

    def _match(self, ehr_system: str, client_identifier: str, ctx: MatchContext) -> MatchResult:
        return match_patient(_hint(ehr_system, client_identifier).hint, ctx)

    def _derive_appointment_url(
        self, ehr_system: str, uid: str, event_url: str | None
    ) -> str | None:
        """Derive the direct URL to the appointment in the EHR."""
        if ehr_system == EhrSystem.SIMPLEPRACTICE:
            # UID is the numeric appointment ID
            return SP_APPOINTMENT_URL.format(uid=uid)
        if ehr_system == EhrSystem.SESSIONS_HEALTH:
            # URL property contains the direct link
            return event_url
        return None

    def _create_appointment(
        self,
        user_id: str,
        ehr_system: str,
        event: ParsedEvent,
        patient_id: str,
    ) -> Appointment:
        """Create an Appointment from a parsed iCal event, for a matched patient."""
        video_link = None
        video_platform = None
        if ehr_system == EhrSystem.SIMPLEPRACTICE and event.url:
            # SP URL property is a video link
            video_link = event.url
            video_platform = "simplepractice"

        return Appointment(
            id=str(uuid.uuid4()),
            user_id=user_id,
            patient_id=patient_id,
            title="Session",
            start_at=event.start_at,
            end_at=event.end_at,
            duration_minutes=event.duration_minutes,
            status=AppointmentStatus.CONFIRMED,
            session_type="individual",
            video_link=video_link,
            video_platform=video_platform,
            ical_uid=event.uid,
            ical_source=ehr_system,
            ical_sync_status="synced",
            ehr_appointment_url=self._derive_appointment_url(ehr_system, event.uid, event.url),
            outside_source=ical_source(ehr_system),
            outside_event_id=event.uid,
            created_at=_now(),
        )

    def _update_if_changed(
        self,
        existing: Appointment,
        event: ParsedEvent,
        ehr_system: str,
    ) -> bool:
        """Update an existing appointment if the iCal event changed."""
        changed = False
        if existing.start_at != event.start_at:
            existing.start_at = event.start_at
            changed = True
        if existing.end_at != event.end_at:
            existing.end_at = event.end_at
            changed = True
        if existing.duration_minutes != event.duration_minutes:
            existing.duration_minutes = event.duration_minutes
            changed = True

        new_url = self._derive_appointment_url(ehr_system, event.uid, event.url)
        if existing.ehr_appointment_url != new_url:
            existing.ehr_appointment_url = new_url
            changed = True

        # Restore if previously soft-deleted but reappeared in feed
        if existing.ical_sync_status == "deleted":
            existing.status = AppointmentStatus.CONFIRMED
            existing.ical_sync_status = "synced"
            changed = True

        if changed:
            existing.updated_at = _now()
            self._appt_repo.update(existing)

        return changed

    def _validate_feed_url(self, ehr_system: str, feed_url: str) -> str:
        """Validate and reconstruct feed URL to prevent SSRF.

        Returns a URL rebuilt from validated components (scheme + host + path)
        so the caller never passes raw user input to urlopen, and it is that
        rebuilt form that is stored and read from then on.

        Only a plain path is accepted. A feed URL from either provider is
        ``https://<host>/<prefix>/<token>`` and nothing else — no username,
        port, query or fragment — so a URL carrying one is more likely
        mistyped than exotic, and is refused rather than trimmed. Dot and
        empty segments are refused after percent-decoding, so the path the
        prefix check read is the path any origin (see ``_fetch_url``) sees.
        """
        allowed_hosts: dict[str, tuple[str, str]] = {
            EhrSystem.SIMPLEPRACTICE: ("secure.simplepractice.com", "/ical/"),
            EhrSystem.SESSIONS_HEALTH: ("app.sessionshealth.com", "/calendars/"),
        }

        if ehr_system not in allowed_hosts:
            msg = f"Unsupported EHR system: {ehr_system}"
            raise ValueError(msg)

        allowed_host, allowed_prefix = allowed_hosts[ehr_system]
        parsed = urlparse(feed_url)

        if parsed.scheme != "https":
            msg = f"Feed URL must use HTTPS (got {parsed.scheme!r})"
            raise ValueError(msg)
        if parsed.hostname != allowed_host:
            msg = f"Feed URL hostname must be {allowed_host} (got {parsed.hostname!r})"
            raise ValueError(msg)
        if parsed.username is not None or parsed.password is not None:
            raise ValueError("Feed URL must not carry a username or password")
        if parsed.port is not None:
            raise ValueError("Feed URL must not name a port")
        if parsed.query or parsed.fragment:
            raise ValueError("Feed URL must not carry a query or fragment")
        if not parsed.path.startswith(allowed_prefix):
            msg = f"Feed URL path must start with {allowed_prefix}"
            raise ValueError(msg)
        if any(segment in ("", ".", "..") for segment in unquote(parsed.path).split("/")[1:]):
            raise ValueError("Feed URL path must not contain empty or dot segments")

        # Reconstruct from validated parts — never pass raw user input to urlopen
        return f"https://{allowed_host}{parsed.path}"

    def _extract_csv(self, file_content: bytes, filename: str) -> str | None:
        """Extract clients.csv from a zip file or return raw CSV content."""
        if filename.endswith(".zip"):
            try:
                with zipfile.ZipFile(io.BytesIO(file_content)) as zf:
                    for name in zf.namelist():
                        if name.endswith("clients.csv"):
                            return zf.read(name).decode("utf-8")
                return None
            except zipfile.BadZipFile:
                return None
        if filename.endswith(".csv"):
            return file_content.decode("utf-8")
        return None

    _CSV_FIELD_MAP: ClassVar[dict[str, str]] = {
        "Email": "email",
        "Phone Number": "phone",
        "Birth Date": "date_of_birth",
        "Diagnosis": "diagnosis",
    }

    def _update_patient_from_csv(self, patient: Any, row: dict[str, str]) -> bool:
        """Update existing patient with newly populated CSV fields. Returns True if changed."""
        changed = False
        for csv_col, attr in self._CSV_FIELD_MAP.items():
            csv_val = row.get(csv_col, "").strip() or None
            if csv_val and not getattr(patient, attr):
                setattr(patient, attr, csv_val)
                changed = True

        # Update status if patient was inactive but CSV says active
        csv_active = row.get("Active", "Y").strip().upper() == "Y"
        if csv_active and patient.status == "inactive":
            patient.status = "active"
            changed = True

        if changed:
            patient.updated_at = _now()
            self._patient_repo.update(patient)
        return changed

    def _create_patient_from_csv(self, row: dict[str, str]) -> Any:
        """Create a Patient dataclass from a CSV row.

        Ownership lives in patient_clinicians, not on the Patient row,
        so this helper no longer needs the importing clinician's
        ``user_id`` — the caller supplies that to
        ``patient_repo.create(patient, user_id)``.
        """
        from ..models.patient import Patient

        now = _now()
        return Patient(
            id=str(uuid.uuid4()),
            first_name=row.get("First Name", "").strip(),
            last_name=row.get("Last Name", "").strip(),
            email=row.get("Email", "").strip() or None,
            phone=row.get("Phone Number", "").strip() or None,
            date_of_birth=row.get("Birth Date", "").strip() or None,
            diagnosis=row.get("Diagnosis", "").strip() or None,
            status="active" if row.get("Active", "Y").strip().upper() == "Y" else "inactive",
            created_at=now,
            updated_at=now,
        )


def _fetch_url(feed_url: str) -> str:
    """Where a feed URL that has passed the allowlist is actually read from.

    Ordinarily the URL itself. A deployment that must not reach the provider
    — the end-to-end stack, whose feeds are served by a stand-in — names an
    origin in ``ical_feed_base_url``, and the feed's own path is read from
    there instead. The URL has already been held to the provider's host and
    path; this swaps only the origin, so the check stays exactly as strict.
    """
    origin = get_settings().ical_feed_base_url
    if not origin:
        return feed_url
    # A validated URL is scheme, host and a plain path; nothing else to carry.
    return f"{origin.rstrip('/')}{urlparse(feed_url).path}"


@dataclass(frozen=True)
class FeedIdentity:
    """How a feed event's client is remembered, and what its title says about them."""

    identifier: str
    hint: PatientHint
    kind: FeedTitleKind


def _hint(ehr_system: str, client_identifier: str) -> FeedIdentity:
    """What a feed's client identifier says about the client.

    A remembered answer for the identifier always counts. Beyond that,
    SimplePractice writes either initials or a full name; Sessions Health
    writes a client code, or a full name when the calendar is set to show
    names. Other sources match on a remembered answer only.
    """
    hint = PatientHint(source=ehr_system, source_identifier=client_identifier)
    if ehr_system == EhrSystem.SIMPLEPRACTICE:
        if _SP_INITIALS_RE.match(client_identifier + " Appointment"):
            initials = hint.model_copy(update={"initials": client_identifier})
            return FeedIdentity(client_identifier, initials, "initials")
        return FeedIdentity(
            client_identifier, hint.model_copy(update={"full_name": client_identifier}), "name"
        )
    if ehr_system == EhrSystem.SESSIONS_HEALTH:
        if _SH_CODE_RE.match(client_identifier):
            return FeedIdentity(client_identifier, hint, "code")
        return FeedIdentity(
            client_identifier, hint.model_copy(update={"full_name": client_identifier}), "name"
        )
    return FeedIdentity(client_identifier, hint, "code")


def feed_identity(ehr_system: str, summary: str) -> FeedIdentity:
    """How a feed event's client is remembered, and what its title says about them."""
    return _hint(ehr_system, ICalSyncService._extract_client_identifier(ehr_system, summary))


def _title_style(ehr_system: str, events: Iterable[ParsedEvent]) -> TitleStyle | None:
    """How a read's titles name clients, for the settings screen.

    Initials only when every appointment title is initials: a feed showing
    names is set up as well as it can be. A SimplePractice event that isn't an
    appointment ("Lunch") says nothing about the setting and is left out.
    """
    kinds = {
        feed_identity(ehr_system, event.summary).kind
        for event in events
        if ehr_system != EhrSystem.SIMPLEPRACTICE or _SP_FULLNAME_RE.match(event.summary)
    }
    if not kinds:
        return None
    if kinds == {"initials"}:
        return "initials"
    if "name" in kinds:
        return "names"
    return "codes"
