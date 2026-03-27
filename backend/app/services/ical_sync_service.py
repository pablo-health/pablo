# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""iCal calendar feed sync service for EHR integration.

Fetches iCal feeds from SimplePractice and Sessions Health, parses events,
and syncs them into Pablo's appointment system with client matching.

HIPAA Compliance:
- Feed URLs encrypted at rest (AES-256-GCM) — they are bearer-token-equivalent
- No PHI (client names, feed URLs, response bodies) in log messages
- Appointment titles stored as generic "Session" (not raw SUMMARY)
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
from typing import TYPE_CHECKING, Any
from urllib.request import Request, urlopen

from icalendar import Calendar

from ..models.enums import EhrSystem
from ..repositories.ical_client_mapping import ICalClientMapping, ICalClientMappingRepository
from ..repositories.ical_sync_config import ICalSyncConfig, ICalSyncConfigRepository
from ..scheduling_engine.models.appointment import Appointment, AppointmentStatus
from .token_encryption import decrypt_tokens, encrypt_tokens

if TYPE_CHECKING:
    from ..models.patient import Patient
    from ..repositories.patient import PatientRepository
    from ..scheduling_engine.repositories.appointment import AppointmentRepository

logger = logging.getLogger(__name__)

FETCH_TIMEOUT_SECONDS = 30
SP_APPOINTMENT_URL = "https://secure.simplepractice.com/appointments/{uid}"

# SimplePractice SUMMARY patterns
_SP_INITIALS_RE = re.compile(r"^([A-Z])\.([A-Z])\.\s+Appointment$")
_SP_FULLNAME_RE = re.compile(r"^(.+?)\s+Appointment$")

# Sessions Health client code pattern
_SH_CODE_RE = re.compile(r"^SH(\d+)$")


def _now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


@dataclass
class ParsedEvent:
    """A parsed VEVENT from an iCal feed."""

    uid: str
    summary: str
    start_at: str  # ISO 8601 UTC
    end_at: str  # ISO 8601 UTC
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
    last_synced_at: str | None = None
    last_sync_error: str | None = None


@dataclass
class ImportResult:
    """Result of importing clients from a CSV/zip export."""

    imported: int = 0
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
        mapping_repo: ICalClientMappingRepository,
    ) -> None:
        self._config_repo = config_repo
        self._appt_repo = appointment_repo
        self._patient_repo = patient_repo
        self._mapping_repo = mapping_repo

    def configure(
        self, user_id: str, ehr_system: str, feed_url: str
    ) -> ConfigureResult:
        """Validate, test-fetch, encrypt, and store the iCal feed URL."""
        self._validate_feed_url(ehr_system, feed_url)

        # Test-fetch to verify the URL works
        ical_data = self._fetch_feed(feed_url)
        events = self._parse_events(ical_data)

        encrypted = encrypt_tokens({"feed_url": feed_url})
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
            )
            for c in configs
        ]

    def sync(
        self, user_id: str, ehr_system: str | None = None
    ) -> list[SyncResult]:
        """Sync one or all configured iCal sources."""
        configs = self._config_repo.list_by_user(user_id)
        if ehr_system:
            configs = [c for c in configs if c.ehr_system == ehr_system]

        results = []
        for config in configs:
            try:
                result = self._sync_source(user_id, config)
                self._config_repo.update_sync_status(
                    user_id, config.ehr_system, error=None
                )
                results.append(result)
            except Exception:
                logger.exception("iCal sync failed for source")
                error_msg = "Sync failed — could not fetch or parse feed"
                self._config_repo.update_sync_status(
                    user_id, config.ehr_system, error=error_msg
                )
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
        """Manually map a client identifier to a patient and update appointments."""
        mapping = ICalClientMapping(
            user_id=user_id,
            ehr_system=ehr_system,
            client_identifier=client_identifier,
            patient_id=patient_id,
        )
        self._mapping_repo.save(mapping)

        # Update existing appointments with this identifier
        appointments = self._appt_repo.list_by_ical_source(user_id, ehr_system)
        for appt in appointments:
            if appt.patient_id == "" and self._get_client_identifier(
                ehr_system, appt.notes or ""
            ) == client_identifier:
                appt.patient_id = patient_id
                appt.updated_at = _now()
                self._appt_repo.update(appt)

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

        all_patients, _ = self._patient_repo.list_by_user(
            user_id, page=1, page_size=10000
        )
        existing = {
            (p.first_name.lower(), p.last_name.lower()) for p in all_patients
        }

        for row_num, row in enumerate(reader, start=1):
            first_name = row.get("First Name", "").strip()
            last_name = row.get("Last Name", "").strip()
            if not first_name or not last_name:
                result.errors.append(f"Row {row_num}: missing name")
                continue

            key = (first_name.lower(), last_name.lower())
            if key in existing:
                result.skipped += 1
            else:
                patient = self._create_patient_from_csv(user_id, row)
                self._patient_repo.create(patient)
                existing.add(key)
                result.imported += 1

            # Auto-create SH code mapping for Sessions Health
            if ehr_system == EhrSystem.SESSIONS_HEALTH:
                sh_code = f"SH{row_num:05d}"
                # Find the patient (may have just been created or already existed)
                patient_id = self._find_patient_by_name(
                    all_patients, first_name, last_name
                )
                if patient_id is None:
                    # Reload to include just-created patients
                    all_patients, _ = self._patient_repo.list_by_user(
                        user_id, page=1, page_size=10000
                    )
                    patient_id = self._find_patient_by_name(
                        all_patients, first_name, last_name
                    )
                if patient_id:
                    mapping = ICalClientMapping(
                        user_id=user_id,
                        ehr_system=ehr_system,
                        client_identifier=sh_code,
                        patient_id=patient_id,
                    )
                    self._mapping_repo.save(mapping)
                    result.mappings_created += 1

        return result

    # --- Private helpers ---

    def _sync_source(
        self, user_id: str, config: ICalSyncConfig
    ) -> SyncResult:
        """Sync a single iCal source."""
        tokens = decrypt_tokens(config.encrypted_feed_url)
        feed_url = tokens["feed_url"]

        ical_data = self._fetch_feed(feed_url)
        feed_events = {e.uid: e for e in self._parse_events(ical_data)}

        existing_appts = self._appt_repo.list_by_ical_source(
            user_id, config.ehr_system
        )
        existing_by_uid = {a.ical_uid: a for a in existing_appts if a.ical_uid}

        # Load mappings and patients for client matching
        mappings = {
            m.client_identifier: m.patient_id
            for m in self._mapping_repo.list_by_source(user_id, config.ehr_system)
        }
        all_patients, _ = self._patient_repo.list_by_user(
            user_id, page=1, page_size=10000
        )

        result = SyncResult()

        # Create or update
        for uid, event in feed_events.items():
            if uid in existing_by_uid:
                updated = self._update_if_changed(
                    existing_by_uid[uid], event, config.ehr_system
                )
                if updated:
                    result.updated += 1
                else:
                    result.unchanged += 1
            else:
                client_id = self._extract_client_identifier(
                    config.ehr_system, event.summary
                )
                patient_id = self._match_patient(
                    config.ehr_system, client_id, mappings, all_patients
                )
                appt = self._create_appointment(
                    user_id, config.ehr_system, event, patient_id
                )
                # Store identifier in notes for later resolution
                if client_id:
                    appt.notes = f"ical_client:{client_id}"
                self._appt_repo.create(appt)
                result.created += 1

                if not patient_id:
                    result.unmatched_events.append({
                        "ical_uid": uid,
                        "client_identifier": client_id or "unknown",
                        "start_at": event.start_at,
                        "ehr_appointment_url": self._derive_appointment_url(
                            config.ehr_system, uid, event.url
                        ) or "",
                    })

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
            result.created, result.updated, result.deleted, result.unchanged,
        )
        return result

    def _fetch_feed(self, feed_url: str) -> str:
        """Fetch iCal feed data via HTTP GET."""
        req = Request(feed_url, headers={"User-Agent": "Pablo/1.0"})  # noqa: S310
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

            events.append(ParsedEvent(
                uid=uid,
                summary=summary,
                start_at=start_utc.isoformat().replace("+00:00", "Z"),
                end_at=end_utc.isoformat().replace("+00:00", "Z"),
                duration_minutes=duration,
                url=url,
            ))
        return events

    def _extract_client_identifier(
        self, ehr_system: str, summary: str
    ) -> str:
        """Extract the client identifier from the SUMMARY field."""
        if ehr_system == EhrSystem.SIMPLEPRACTICE:
            # Try initials first: "J.A. Appointment"
            m = _SP_INITIALS_RE.match(summary)
            if m:
                return f"{m.group(1)}.{m.group(2)}."
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
            return notes[len("ical_client:"):]
        return ""

    def _match_patient(
        self,
        ehr_system: str,
        client_identifier: str,
        mappings: dict[str, str],
        patients: list[Patient],
    ) -> str:
        """Attempt to match a client identifier to a Pablo patient ID."""
        # Check saved mappings first
        if client_identifier in mappings:
            return mappings[client_identifier]

        # SimplePractice: try matching by initials or full name
        if ehr_system == EhrSystem.SIMPLEPRACTICE:
            return self._match_sp_patient(client_identifier, patients)

        return ""

    def _match_sp_patient(
        self, identifier: str, patients: list[Patient]
    ) -> str:
        """Match a SimplePractice identifier to a patient."""
        # Initials format: "J.A."
        m = _SP_INITIALS_RE.match(identifier + " Appointment")
        if m:
            first_initial = m.group(1).upper()
            last_initial = m.group(2).upper()
            matches = [
                p for p in patients
                if p.first_name and p.last_name
                and p.first_name[0].upper() == first_initial
                and p.last_name[0].upper() == last_initial
            ]
            if len(matches) == 1:
                return matches[0].id
            return ""

        # Full name format: "Jane Adams"
        _min_name_parts = 2
        parts = identifier.split()
        if len(parts) >= _min_name_parts:
            first = parts[0].lower()
            last = parts[-1].lower()
            matches = [
                p for p in patients
                if p.first_name_lower == first and p.last_name_lower == last
            ]
            if len(matches) == 1:
                return matches[0].id

        return ""

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
        """Create an Appointment from a parsed iCal event."""
        video_link = None
        video_platform = None
        if ehr_system == EhrSystem.SIMPLEPRACTICE and event.url:
            # SP URL property is a video link
            video_link = event.url
            video_platform = "simplepractice"

        return Appointment(
            id=str(uuid.uuid4()),
            user_id=user_id,
            patient_id=patient_id or "",
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
            ehr_appointment_url=self._derive_appointment_url(
                ehr_system, event.uid, event.url
            ),
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

    def _validate_feed_url(self, ehr_system: str, feed_url: str) -> None:
        """Basic validation of the feed URL."""
        if ehr_system == EhrSystem.SIMPLEPRACTICE:
            if not feed_url.startswith("https://secure.simplepractice.com/ical/"):
                msg = "SimplePractice feed URL must start with https://secure.simplepractice.com/ical/"
                raise ValueError(msg)
        elif ehr_system == EhrSystem.SESSIONS_HEALTH:
            if not feed_url.startswith("https://app.sessionshealth.com/calendars/"):
                msg = "Sessions Health feed URL must start with https://app.sessionshealth.com/calendars/"
                raise ValueError(msg)
        else:
            msg = f"Unsupported EHR system: {ehr_system}"
            raise ValueError(msg)

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

    def _create_patient_from_csv(
        self, user_id: str, row: dict[str, str]
    ) -> Any:
        """Create a Patient dataclass from a CSV row."""
        from ..models.patient import Patient

        now = _now()
        return Patient(
            id=str(uuid.uuid4()),
            user_id=user_id,
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

    @staticmethod
    def _find_patient_by_name(
        patients: list[Patient], first_name: str, last_name: str
    ) -> str | None:
        """Find a patient by first and last name."""
        for p in patients:
            if (
                p.first_name.lower() == first_name.lower()
                and p.last_name.lower() == last_name.lower()
            ):
                return p.id
        return None
