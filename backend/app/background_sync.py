# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""In-process background calendar sync for self-hosted deployments.

Runs every 15 minutes inside the FastAPI process. For managed
deployments, Cloud Scheduler + Cloud Tasks handles this instead (see
internal.py).

Single-worker uvicorn guarantees no duplicate runs.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

from .repositories import (
    get_appointment_repository,
    get_availability_rule_repository,
    get_external_calendar_event_repository,
    get_google_calendar_token_repository,
    get_ical_sync_config_repository,
    get_patient_repository,
    get_patient_source_mapping_repository,
    get_user_repository,
)
from .services.google_calendar_service import (
    GoogleCalendarService,
    google_consent_surface,
)
from .services.ical_sync_service import ICalSyncService
from .services.reminder_service import ReminderService
from .services.sync_scheduler_service import (
    SyncSchedulerService,
    _is_within_working_hours,
    _user_sync_states,
    working_rules,
)
from .settings import get_settings

if TYPE_CHECKING:
    from .repositories.google_calendar_token import GoogleCalendarTokenRepository
    from .repositories.ical_sync_config import ICalSyncConfigRepository
    from .repositories.user import UserRepository

logger = logging.getLogger(__name__)

SYNC_INTERVAL_SECONDS = 15 * 60  # 15 minutes


async def calendar_sync_loop() -> None:
    """Background loop: sync all connected calendars every 15 minutes."""
    while True:
        await asyncio.sleep(SYNC_INTERVAL_SECONDS)
        try:
            _run_sync_cycle()
        except Exception:
            logger.exception("Background calendar sync loop error")


def build_sync_scheduler(
    *,
    ical_config_repo: ICalSyncConfigRepository | None = None,
    google_token_repo: GoogleCalendarTokenRepository | None = None,
    user_repo: UserRepository | None = None,
) -> SyncSchedulerService:
    """The scheduler wired to this deployment's repositories and providers.

    One construction, shared by the loop below and by the route that runs
    the same pass for one account on request, so the two cannot drift. The
    loop hands in the repositories it reads on its own; the route takes the
    defaults.
    """
    ical_config_repo = ical_config_repo or get_ical_sync_config_repository()
    google_token_repo = google_token_repo or get_google_calendar_token_repository()
    appointment_repo = get_appointment_repository()

    user_repo = user_repo or get_user_repository()

    return SyncSchedulerService(
        ical_config_repo=ical_config_repo,
        google_token_repo=google_token_repo,
        user_repo=user_repo,
        ical_sync_service=ICalSyncService(
            config_repo=ical_config_repo,
            appointment_repo=appointment_repo,
            patient_repo=get_patient_repository(),
            mapping_repo=get_patient_source_mapping_repository(),
            external_events=get_external_calendar_event_repository(),
            users=user_repo,
        ),
        google_calendar_service=GoogleCalendarService.from_surface(
            google_consent_surface(get_settings()),
            token_repo=google_token_repo,
            appointment_repo=appointment_repo,
        ),
        reminder_service=ReminderService(appointment_repo),
        appointment_repo=appointment_repo,
        availability_rule_repo=get_availability_rule_repository(),
    )


def _run_sync_cycle() -> None:
    """Execute one sync cycle for all eligible users."""
    settings = get_settings()

    ical_config_repo = get_ical_sync_config_repository()
    google_token_repo = get_google_calendar_token_repository()
    user_repo = get_user_repository()
    service = build_sync_scheduler(
        ical_config_repo=ical_config_repo,
        google_token_repo=google_token_repo,
        user_repo=user_repo,
    )

    rules = get_availability_rule_repository()
    states = _user_sync_states(
        ical_config_repo.list_all(),
        google_token_repo.list_all(),
        settings.calendar_sync_max_consecutive_failures,
    )

    synced = 0
    for user_id, state in states.items():
        # Every source past the failure limit: nothing left to read. One
        # that is still read is, and execute() leaves out the rest.
        if state.all_paused:
            continue
        prefs = user_repo.get_preferences(user_id)
        if not _is_within_working_hours(prefs, working_rules(rules, user_id)):
            continue

        try:
            service.execute(user_id)
            synced += 1
        except Exception:
            logger.exception("Background sync failed for a user")

    if synced:
        logger.info("Background calendar sync: synced %d users", synced)
