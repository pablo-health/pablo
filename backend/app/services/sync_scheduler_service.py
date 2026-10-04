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
from ..settings import get_settings
from .google_calendar_follow import SYNC_COMPONENT, GoogleChangeFollower
from .google_calendar_service import CalendarGoneError

if TYPE_CHECKING:
    from ..models.user import UserPreferences
    from ..repositories.google_calendar_token import GoogleCalendarTokenRepository
    from ..repositories.ical_sync_config import ICalSyncConfigRepository
    from ..repositories.user import UserRepository
    from ..scheduling_engine.repositories.appointment import AppointmentRepository
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
        self._follower = GoogleChangeFollower(appointment_repo, google_calendar_service)

    def dispatch(self) -> DispatchSummary:
        """Fan out sync tasks to Cloud Tasks — one per eligible user.

        1. Collect all ical_sync_configs and google_calendar_tokens.
        2. Build set of unique user_ids.
        3. For each user: check working hours + circuit breaker → enqueue.
        """
        settings = get_settings()
        summary = DispatchSummary()

        ical_configs = self._ical_config_repo.list_all()
        google_tokens = self._google_token_repo.list_all()

        # Build per-user state: which sources they have + max error count
        user_sources: dict[str, _UserSyncState] = {}
        for cfg in ical_configs:
            state = user_sources.setdefault(cfg.user_id, _UserSyncState())
            state.has_ical = True
            state.max_error_count = max(state.max_error_count, cfg.consecutive_error_count)
        for tok in google_tokens:
            state = user_sources.setdefault(tok.user_id, _UserSyncState())
            state.has_google = True
            state.max_error_count = max(state.max_error_count, tok.consecutive_error_count)

        max_failures = settings.calendar_sync_max_consecutive_failures
        # One query for every dispatched user's preferences instead of one
        # per user inside the loop.
        prefs_by_user = self._user_repo.get_preferences_many(list(user_sources))

        for user_id, state in user_sources.items():
            # Circuit breaker: skip users whose sources all exceed max failures
            if state.max_error_count >= max_failures:
                summary.skipped_circuit_breaker += 1
                continue

            # Working hours filter
            prefs = prefs_by_user[user_id]
            if not _is_within_working_hours(prefs):
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

    def execute(self, user_id: str) -> ExecuteSummary:
        """Sync one user's calendars and check reminders.

        Called by Cloud Tasks with a single user_id. Each source is synced
        independently — one failure doesn't block the others.
        """
        summary = ExecuteSummary()

        # 1. iCal feed sync
        try:
            results = self._ical_sync_service.sync(user_id)
            for result in results:
                if result.errors:
                    summary.ical_errors += 1
                else:
                    summary.ical_sources_synced += 1
        except Exception:
            logger.exception("iCal sync failed for scheduled run")
            summary.ical_errors += 1

        # 2. Google Calendar sync
        google_token = self._google_token_repo.get(user_id)
        if google_token:
            try:
                changes = self._google_calendar_service.sync_from_google(user_id)
                summary.google_synced = True
                summary.google_changes_processed = self._follow_google_changes(user_id, changes)
            except CalendarGoneError:
                try:
                    summary.google_synced = self._rebuild_google_calendar(user_id)
                except Exception:
                    logger.exception("Recreating the Google calendar failed for scheduled run")
                    summary.google_error = True
            except Exception:
                logger.exception("Google Calendar sync failed for scheduled run")
                summary.google_error = True

            # 2b. Sessions another service puts on the followed calendar
            if google_token.follow_calendar_id:
                try:
                    summary.outside_sessions_followed = self._follow_calendar(user_id)
                except Exception:
                    logger.exception("Following a calendar failed for scheduled run")
                    summary.google_error = True

        # 3. Reminders
        try:
            reminder_result = self._reminder_service.check_and_send_reminders(user_id)
            summary.reminders_sent = reminder_result.get("24h_sent", 0) + reminder_result.get(
                "1h_sent", 0
            )
        except Exception:
            logger.exception("Reminder check failed for scheduled run")

        return summary

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
    max_error_count: int = 0


# A per-user working-hours window now lives in availability rules (one per
# day, no single start/end), not in preferences — too granular for a dispatch
# throttle that only needs an approximate daytime window per timezone.
_DEFAULT_WINDOW_START_HOUR = 8
_DEFAULT_WINDOW_END_HOUR = 18


def _is_within_working_hours(prefs: UserPreferences) -> bool:
    """Check if the current time falls within a daytime window ±1 hour."""
    try:
        tz = ZoneInfo(prefs.timezone)
    except (ZoneInfoNotFoundError, KeyError):
        # Invalid timezone — default to syncing (don't skip)
        return True

    user_now = datetime.now(tz)
    window_start = max(_DEFAULT_WINDOW_START_HOUR - 1, 0)
    window_end = min(_DEFAULT_WINDOW_END_HOUR + 1, 24)
    return window_start <= user_now.hour < window_end


def dispatch_sync_tasks() -> DispatchSummary:
    """Discover all users with connected calendars and enqueue sync tasks.

    Reads all sync configs via list_all() — requires BYPASSRLS on the DB
    service account role for Postgres deployments.
    """
    from ..repositories import (
        get_google_calendar_token_repository,
        get_ical_sync_config_repository,
        get_user_repository,
    )

    settings = get_settings()
    summary = DispatchSummary()

    ical_configs = get_ical_sync_config_repository().list_all()
    google_tokens = get_google_calendar_token_repository().list_all()
    user_repo = get_user_repository()

    user_sources: dict[str, _UserSyncState] = {}
    for cfg in ical_configs:
        state = user_sources.setdefault(cfg.user_id, _UserSyncState())
        state.has_ical = True
        state.max_error_count = max(state.max_error_count, cfg.consecutive_error_count)
    for tok in google_tokens:
        state = user_sources.setdefault(tok.user_id, _UserSyncState())
        state.has_google = True
        state.max_error_count = max(state.max_error_count, tok.consecutive_error_count)

    max_failures = settings.calendar_sync_max_consecutive_failures

    for user_id, state in user_sources.items():
        if state.max_error_count >= max_failures:
            summary.skipped_circuit_breaker += 1
            continue
        prefs = user_repo.get_preferences(user_id)
        if not _is_within_working_hours(prefs):
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
