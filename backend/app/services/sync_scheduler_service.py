# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Periodic calendar sync orchestrator via Cloud Tasks fan-out.

Called by Cloud Scheduler every 15 minutes. Dispatches one Cloud Task per
eligible user (within working hours, circuit breaker not tripped).

HIPAA Compliance:
- Logs aggregate counts only — never user IDs, feed URLs, or PHI.
- All calendar data stays within BAA-covered GCP services.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime, tzinfo
from typing import TYPE_CHECKING, Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from ..calendar_providers.source_identity import GOOGLE_CALENDAR_SOURCE
from ..models.audit import ACTOR_TYPE_SYSTEM, AuditAction
from ..scheduling_engine.models.availability import RuleType
from ..settings import get_settings
from ..utcnow import utc_now
from .google_calendar_follow import SYNC_COMPONENT, GoogleChangeFollower
from .google_calendar_service import CalendarGoneError, read_error_kind

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence

    from ..models.user import UserPreferences
    from ..repositories.google_calendar_token import (
        GoogleCalendarTokenDoc,
        GoogleCalendarTokenRepository,
    )
    from ..repositories.ical_sync_config import ICalSyncConfig, ICalSyncConfigRepository
    from ..repositories.user import UserRepository
    from ..scheduling_engine.models.availability import AvailabilityRule
    from ..scheduling_engine.repositories.appointment import AppointmentRepository
    from ..scheduling_engine.repositories.availability_rule import AvailabilityRuleRepository
    from ..services.audit_service import AuditService
    from ..services.google_calendar_service import GoogleCalendarService
    from ..services.ical_sync_service import ICalSyncService
    from ..services.reminder_service import ReminderService
    from .outside_sessions import OutsideSessions

logger = logging.getLogger(__name__)

#: Why the audit trail shows an appointment the system made on its own.
BOOKED_FROM_MAIN_CALENDAR = "booked_from_main_calendar"
#: Beside that reason when the event's title named the chart, rather than a
#: remembered answer: the matcher's word for a match on a full name.
MATCHED_ON_FULL_NAME = "full_name"


@dataclass
class DispatchSummary:
    """Result of the dispatch phase — how many users were enqueued vs skipped."""

    enqueued: int = 0
    skipped_outside_hours: int = 0
    skipped_circuit_breaker: int = 0
    skipped_no_configs: int = 0
    errors: int = 0

    def to_dict(self) -> dict[str, int]:
        return {
            "enqueued": self.enqueued,
            "skipped_outside_hours": self.skipped_outside_hours,
            "skipped_circuit_breaker": self.skipped_circuit_breaker,
            "skipped_no_configs": self.skipped_no_configs,
            "errors": self.errors,
        }


@dataclass
class ExecuteSummary:
    """Result of syncing a single user's calendars + reminders."""

    ical_sources_synced: int = 0
    ical_errors: int = 0
    google_synced: bool = False
    google_error: bool = False
    google_paused: bool = False
    google_changes_processed: int = 0
    outside_sessions_followed: int = 0
    reminders_sent: int = 0
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "ical_sources_synced": self.ical_sources_synced,
            "ical_errors": self.ical_errors,
            "google_synced": self.google_synced,
            "google_error": self.google_error,
            "google_paused": self.google_paused,
            "google_changes_processed": self.google_changes_processed,
            "outside_sessions_followed": self.outside_sessions_followed,
            "reminders_sent": self.reminders_sent,
        }


class SyncSchedulerService:
    """Orchestrates periodic calendar sync via Cloud Tasks fan-out.

    Two entry points:
    - dispatch(): Called by Cloud Scheduler. Filters users, enqueues Cloud Tasks.
    - execute(): Called by Cloud Tasks. Syncs one user's calendars + reminders.
    """

    def __init__(
        self,
        ical_config_repo: ICalSyncConfigRepository,
        google_token_repo: GoogleCalendarTokenRepository,
        user_repo: UserRepository,
        ical_sync_service: ICalSyncService,
        google_calendar_service: GoogleCalendarService,
        reminder_service: ReminderService,
        appointment_repo: AppointmentRepository,
        audit_service: AuditService | None = None,
        outside_sessions: OutsideSessions | None = None,
        availability_rule_repo: AvailabilityRuleRepository | None = None,
    ) -> None:
        self._ical_config_repo = ical_config_repo
        self._google_token_repo = google_token_repo
        self._user_repo = user_repo
        self._ical_sync_service = ical_sync_service
        self._google_calendar_service = google_calendar_service
        self._reminder_service = reminder_service
        self._appointment_repo = appointment_repo
        # Resolved when first needed, so a caller built before the follower
        # existed keeps working without passing one.
        self._audit_service = audit_service
        self._outside_sessions = outside_sessions
        self._availability_rule_repo = availability_rule_repo
        self._follower = GoogleChangeFollower(appointment_repo, google_calendar_service)

    def dispatch(self) -> DispatchSummary:
        """Fan out sync tasks to Cloud Tasks — one per eligible user.

        1. Collect all ical_sync_configs and google_calendar_tokens.
        2. Build set of unique user_ids.
        3. For each user: check circuit breaker + working hours → enqueue.
        """
        settings = get_settings()
        summary = DispatchSummary()

        user_sources = _user_sync_states(
            self._ical_config_repo.list_all(),
            self._google_token_repo.list_all(),
            settings.calendar_sync_max_consecutive_failures,
        )
        # One query for every dispatched user's preferences instead of one
        # per user inside the loop.
        prefs_by_user = self._user_repo.get_preferences_many(list(user_sources))

        for user_id, state in user_sources.items():
            # Circuit breaker: skip users whose sources all exceed max failures
            if state.all_paused:
                summary.skipped_circuit_breaker += 1
                continue

            # Working hours filter
            prefs = prefs_by_user[user_id]
            if not _is_within_working_hours(prefs, self._working_rules(user_id)):
                summary.skipped_outside_hours += 1
                continue

            try:
                _enqueue_sync_task(user_id)
                summary.enqueued += 1
            except Exception:
                logger.exception("Failed to enqueue sync task")
                summary.errors += 1

        logger.info(
            "Sync dispatch complete: enqueued=%d skipped_hours=%d skipped_breaker=%d errors=%d",
            summary.enqueued,
            summary.skipped_outside_hours,
            summary.skipped_circuit_breaker,
            summary.errors,
        )
        return summary

    def execute(self, user_id: str, *, on_request: bool = False) -> ExecuteSummary:
        """Sync one user's calendars and check reminders.

        Called by Cloud Tasks with a single user_id. Each source is synced
        independently — one failure doesn't block the others.

        A scheduled pass leaves out any source that has failed
        ``calendar_sync_max_consecutive_failures`` times in a row. One run
        ``on_request`` reads them all: it is how a clinician who pressed for
        a read gets one, and a read that works starts the schedule again.
        """
        summary = ExecuteSummary()
        max_failures = get_settings().calendar_sync_max_consecutive_failures

        # 1. iCal feed sync
        try:
            for result in self._sync_feeds(user_id, None if on_request else max_failures):
                if result.errors:
                    summary.ical_errors += 1
                else:
                    summary.ical_sources_synced += 1
        except Exception:
            logger.exception("iCal sync failed for scheduled run")
            summary.ical_errors += 1

        # 2. Google Calendar sync
        google_token = self._google_token_repo.get(user_id)
        if google_token and not on_request and google_token.consecutive_error_count >= max_failures:
            summary.google_paused = True
        elif google_token:
            failure: Exception | None = None
            try:
                changes = self._google_calendar_service.sync_from_google(user_id)
                summary.google_synced = True
                summary.google_changes_processed = self._follow_google_changes(user_id, changes)
            except CalendarGoneError:
                try:
                    summary.google_synced = self._rebuild_google_calendar(user_id)
                except Exception as exc:
                    _log_google_failure("Recreating the Google calendar failed", exc)
                    summary.google_error = True
                    failure = exc
            except Exception as exc:
                _log_google_failure("Google Calendar read failed", exc)
                summary.google_error = True
                failure = exc

            # 2b. Sessions another service puts on the followed calendar
            if google_token.follow_calendar_id:
                try:
                    summary.outside_sessions_followed = self._follow_calendar(user_id)
                except Exception as exc:
                    _log_google_failure("Following a calendar failed", exc)
                    summary.google_error = True
                    failure = failure or exc

            self._record_google_read(user_id, failure)

        # 3. Reminders
        try:
            reminder_result = self._reminder_service.check_and_send_reminders(user_id)
            summary.reminders_sent = reminder_result.get("24h_sent", 0) + reminder_result.get(
                "1h_sent", 0
            )
        except Exception:
            logger.exception("Reminder check failed for scheduled run")

        return summary

    def _sync_feeds(self, user_id: str, max_failures: int | None) -> list[Any]:
        """Read the user's feeds, leaving out those past ``max_failures``.

        None reads every feed. One feed that keeps failing stops only
        itself: the rest, and the Google calendar, carry on.
        """
        if max_failures is None:
            return list(self._ical_sync_service.sync(user_id))
        configs: list[ICalSyncConfig] = list(self._ical_config_repo.list_by_user(user_id))
        live = [c for c in configs if c.consecutive_error_count < max_failures]
        if len(live) == len(configs):
            return list(self._ical_sync_service.sync(user_id))
        results: list[Any] = []
        for config in live:
            results.extend(self._ical_sync_service.sync(user_id, config.ehr_system))
        return results

    def _record_google_read(self, user_id: str, failure: Exception | None) -> None:
        """Keep how the read went on the connection, for its status to show.

        Only the kind of failure is kept (``read_error_kind``), never its
        message. A failure to record leaves the read's own outcome alone.
        """
        try:
            if failure is None:
                self._google_token_repo.record_read_success(user_id, utc_now())
            else:
                self._google_token_repo.record_read_failure(user_id, read_error_kind(failure))
        except Exception:
            logger.exception("Recording how a Google Calendar read went failed")

    def _working_rules(self, user_id: str) -> Sequence[AvailabilityRule]:
        if self._availability_rule_repo is None:
            from ..repositories import get_availability_rule_repository

            self._availability_rule_repo = get_availability_rule_repository()
        return working_rules(self._availability_rule_repo, user_id)

    def _follow_google_changes(self, user_id: str, changes: list[dict[str, Any]]) -> int:
        """Follow moves and deletions of the sessions Pablo pushed to Google.

        Changes to events Pablo never pushed are ignored. See
        ``google_calendar_follow`` for what is followed and what is held.
        """
        if not changes:
            return 0
        user = self._user_repo.get(user_id)
        if user is None:
            return 0
        return self._follower.follow(user, self._audit(), changes).changed

    def _follow_calendar(self, user_id: str) -> int:
        """Bring in sessions from the followed calendar, and follow the answered ones.

        Only reads: nothing is ever written to an event another service made.
        New events are held, answered or dropped first; then the appointments
        already made for answered events follow their moves and deletions,
        under the same guards as Pablo's own sessions.
        """
        read = self._google_calendar_service.read_main_calendar_changes(user_id)
        # The main calendar, when known: what rows and answers from before
        # calendars were recorded are about.
        main_calendar_id = (
            read.main_calendar_id or self._google_calendar_service.known_main_calendar_id(user_id)
        )
        outside = self._outside().in_zone(self._zone(user_id)).with_main_calendar(main_calendar_id)
        if read.main_calendar_id is not None:
            # Rows from before calendars were recorded came from here.
            outside.claim_unrecorded(user_id, read.main_calendar_id)
        if not read.changes and not read.full:
            return 0
        user = self._user_repo.get(user_id)
        if user is None:
            return 0
        audit = self._audit()
        changes = read.changes
        ingested = outside.ingest_google(user_id, changes, calendar_id=read.calendar_id)
        if read.full and read.window is not None and read.calendar_id is not None:
            present = {str(change.get("google_event_id")) for change in changes}
            changes = changes + outside.reconcile_full_read(
                user_id, present, read.window, read.calendar_id
            )
        for appointment in ingested.booked:
            audit.log_appointment_action(
                AuditAction.APPOINTMENT_CREATED,
                user,
                None,
                appointment.id,
                patient_id=appointment.patient_id,
                changes=(
                    {"reason": BOOKED_FROM_MAIN_CALENDAR, "matched_on": MATCHED_ON_FULL_NAME}
                    if appointment.id in ingested.by_name
                    else {"reason": BOOKED_FROM_MAIN_CALENDAR}
                ),
                actor_type=ACTOR_TYPE_SYSTEM,
                actor_component=SYNC_COMPONENT,
            )
        followed = self._follower.follow(
            user, audit, changes, outside_source=GOOGLE_CALENDAR_SOURCE
        )
        return ingested.held + followed.changed

    def _zone(self, user_id: str) -> tzinfo:
        try:
            return ZoneInfo(self._user_repo.get_preferences(user_id).timezone)
        except (ZoneInfoNotFoundError, KeyError, ValueError, TypeError):
            return UTC

    def _outside(self) -> OutsideSessions:
        if self._outside_sessions is None:
            from ..repositories import (
                get_external_calendar_event_repository,
                get_patient_repository,
                get_patient_source_mapping_repository,
            )
            from .outside_sessions import OutsideSessions

            self._outside_sessions = OutsideSessions(
                get_external_calendar_event_repository(),
                self._appointment_repo,
                get_patient_repository(),
                get_patient_source_mapping_repository(),
                users=self._user_repo,
            )
        return self._outside_sessions

    def _rebuild_google_calendar(self, user_id: str) -> bool:
        """Recreate a deleted Pablo calendar and push upcoming sessions back.

        Nothing is cancelled: every event vanishing at once says the calendar
        went, not that any session did.
        """
        if not self._google_calendar_service.recreate_app_calendar(user_id):
            return False
        pushed = self._follower.repush_upcoming(user_id)
        logger.info("Pushed %d sessions into the recreated Google calendar", pushed)
        return True

    def _audit(self) -> AuditService:
        if self._audit_service is None:
            from .audit_service import get_audit_service

            self._audit_service = get_audit_service()
        return self._audit_service


@dataclass
class _UserSyncState:
    """Tracks per-user sync sources and error state during dispatch."""

    has_ical: bool = False
    has_google: bool = False
    live_sources: int = 0
    """Sources still under the failure limit. A user is skipped only when
    none is: one broken feed must not stop the Google calendar being read."""

    @property
    def all_paused(self) -> bool:
        return self.live_sources == 0


def _user_sync_states(
    ical_configs: Iterable[ICalSyncConfig],
    google_tokens: Iterable[GoogleCalendarTokenDoc],
    max_failures: int,
) -> dict[str, _UserSyncState]:
    """Each user with a source, and how many of their sources are still read."""
    states: dict[str, _UserSyncState] = {}
    for cfg in ical_configs:
        state = states.setdefault(cfg.user_id, _UserSyncState())
        state.has_ical = True
        state.live_sources += cfg.consecutive_error_count < max_failures
    for tok in google_tokens:
        state = states.setdefault(tok.user_id, _UserSyncState())
        state.has_google = True
        state.live_sources += tok.consecutive_error_count < max_failures
    return states


# The window used when a clinician has set no working hours: 08:00-18:00,
# widened by the same margins as a window from their own hours.
_DEFAULT_WINDOW_START_HOUR = 8
_DEFAULT_WINDOW_END_HOUR = 18
#: Read from an hour before the earliest working time...
_WINDOW_LEAD_MINUTES = 60
#: ...to two hours after the latest: a session moved in the evening, after the
#: last client, is on the calendar the next morning.
_WINDOW_TRAIL_MINUTES = 120
_MINUTES_PER_DAY = 24 * 60


def _clock_minutes(value: object) -> int | None:
    """``"HH:MM"`` as minutes after midnight, or None for anything else."""
    hours, sep, minutes = str(value).partition(":")
    if not sep or not hours.isdigit() or not minutes[:2].isdigit():
        return None
    total = int(hours) * 60 + int(minutes[:2])
    return total if 0 <= total <= _MINUTES_PER_DAY else None


def _read_window(rules: Iterable[AvailabilityRule]) -> tuple[int, int]:
    """When in the day the scheduled pass reads a clinician's calendars.

    From an hour before the earliest working time on any day to two hours
    after the latest, in minutes after midnight. With no working hours set,
    the default day. The same earliest and latest the calendar page scrolls
    to (``deriveWorkingHoursWindow``), so the two agree on the working day.
    """
    starts: list[int] = []
    ends: list[int] = []
    for rule in rules:
        if rule.rule_type != RuleType.WORKING_HOURS:
            continue
        start = _clock_minutes(rule.params.get("start"))
        end = _clock_minutes(rule.params.get("end"))
        if start is None or end is None or end <= start:
            continue
        starts.append(start)
        ends.append(end)
    if not starts:
        earliest, latest = _DEFAULT_WINDOW_START_HOUR * 60, _DEFAULT_WINDOW_END_HOUR * 60
    else:
        earliest, latest = min(starts), max(ends)
    trail = _WINDOW_TRAIL_MINUTES if starts else _WINDOW_LEAD_MINUTES
    return (
        max(earliest - _WINDOW_LEAD_MINUTES, 0),
        min(latest + trail, _MINUTES_PER_DAY),
    )


def _is_within_working_hours(
    prefs: UserPreferences, rules: Iterable[AvailabilityRule] = ()
) -> bool:
    """Whether now, in the user's zone, falls inside their read window."""
    try:
        tz = ZoneInfo(prefs.timezone)
    except (ZoneInfoNotFoundError, KeyError):
        # Invalid timezone — default to syncing (don't skip)
        return True

    user_now = datetime.now(tz)
    minute = user_now.hour * 60 + user_now.minute
    start, end = _read_window(rules)
    return start <= minute < end


def working_rules(repo: AvailabilityRuleRepository, user_id: str) -> Sequence[AvailabilityRule]:
    """The user's availability rules, or none when they cannot be read.

    None falls back to the default window: a dispatch never stops because
    one clinician's rules could not be loaded.
    """
    try:
        return repo.list_by_user(user_id)
    except Exception:
        logger.exception("Reading availability rules for the sync window failed")
        return ()


def _log_google_failure(what: str, exc: Exception) -> None:
    """Log a failed Google read by its kind and class, never its message.

    The message of an error from Google can carry the request and parts of
    the answer, and with them what is on the calendar.
    """
    logger.warning("%s: %s (%s)", what, read_error_kind(exc), type(exc).__name__)


def dispatch_sync_tasks() -> DispatchSummary:
    """Discover all users with connected calendars and enqueue sync tasks.

    Reads all sync configs via list_all() — requires BYPASSRLS on the DB
    service account role for Postgres deployments.
    """
    from ..repositories import (
        get_availability_rule_repository,
        get_google_calendar_token_repository,
        get_ical_sync_config_repository,
        get_user_repository,
    )

    settings = get_settings()
    summary = DispatchSummary()

    user_repo = get_user_repository()
    rules = get_availability_rule_repository()
    user_sources = _user_sync_states(
        get_ical_sync_config_repository().list_all(),
        get_google_calendar_token_repository().list_all(),
        settings.calendar_sync_max_consecutive_failures,
    )

    for user_id, state in user_sources.items():
        if state.all_paused:
            summary.skipped_circuit_breaker += 1
            continue
        prefs = user_repo.get_preferences(user_id)
        if not _is_within_working_hours(prefs, working_rules(rules, user_id)):
            summary.skipped_outside_hours += 1
            continue
        try:
            _enqueue_sync_task(user_id)
            summary.enqueued += 1
        except Exception:
            logger.exception("Failed to enqueue sync task")
            summary.errors += 1

    logger.info(
        "Sync dispatch complete: enqueued=%d skipped_hours=%d skipped_breaker=%d errors=%d",
        summary.enqueued,
        summary.skipped_outside_hours,
        summary.skipped_circuit_breaker,
        summary.errors,
    )
    return summary


def _enqueue_sync_task(user_id: str) -> None:
    """Enqueue a Cloud Task to sync a single user's calendars.

    In development mode, logs the task instead of enqueuing.
    """
    settings = get_settings()

    if settings.is_development:
        logger.info("Dev mode: would enqueue sync task for user (not logging ID)")
        return

    from google.cloud import tasks_v2

    client = tasks_v2.CloudTasksClient()
    parent = client.queue_path(
        settings.gcp_project_id,
        settings.calendar_sync_task_location,
        settings.calendar_sync_task_queue,
    )

    # Build the HTTP target — hits the execute endpoint on the same backend
    backend_url = settings.transcription_backend_callback_url
    if not backend_url:
        backend_url = settings.app_url.replace(":3000", ":8000")

    task = tasks_v2.Task(
        http_request=tasks_v2.HttpRequest(
            http_method=tasks_v2.HttpMethod.POST,
            url=f"{backend_url}/api/internal/sync-calendars/execute",
            headers={"Content-Type": "application/json"},
            body=json.dumps({"user_id": user_id}).encode(),
            oidc_token=tasks_v2.OidcToken(
                service_account_email=(
                    f"calendar-sync-scheduler@{settings.gcp_project_id}.iam.gserviceaccount.com"
                ),
                audience=backend_url,
            ),
        ),
    )

    client.create_task(
        parent=parent,
        task=task,
    )
