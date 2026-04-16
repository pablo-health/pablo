# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""In-process background calendar sync for self-hosted (Pablo Core) deployments.

Runs every 15 minutes inside the FastAPI process. For SaaS deployments,
Cloud Scheduler + Cloud Tasks handles this instead (see internal.py).

Single-worker uvicorn guarantees no duplicate runs.
"""

from __future__ import annotations

import asyncio
import logging

from .repositories import (
    get_appointment_repository,
    get_google_calendar_token_repository,
    get_ical_client_mapping_repository,
    get_ical_sync_config_repository,
    get_patient_repository,
    get_user_repository,
)
from .services.google_calendar_service import GoogleCalendarService
from .services.ical_sync_service import ICalSyncService
from .services.reminder_service import ReminderService
from .services.sync_scheduler_service import SyncSchedulerService, _is_within_working_hours
from .settings import get_settings

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


def _run_sync_cycle() -> None:
    """Execute one sync cycle for all eligible users."""
    settings = get_settings()

    ical_config_repo = get_ical_sync_config_repository()
    google_token_repo = get_google_calendar_token_repository()
    user_repo = get_user_repository()
    appointment_repo = get_appointment_repository()

    service = SyncSchedulerService(
        ical_config_repo=ical_config_repo,
        google_token_repo=google_token_repo,
        user_repo=user_repo,
        ical_sync_service=ICalSyncService(
            config_repo=ical_config_repo,
            appointment_repo=appointment_repo,
            patient_repo=get_patient_repository(),
            mapping_repo=get_ical_client_mapping_repository(),
        ),
        google_calendar_service=GoogleCalendarService(
            token_repo=google_token_repo,
            appointment_repo=appointment_repo,
            client_id=settings.google_calendar_client_id,
            client_secret=settings.google_calendar_client_secret.get_secret_value(),
        ),
        reminder_service=ReminderService(appointment_repo),
    )

    configs = ical_config_repo.list_all()
    tokens = google_token_repo.list_all()
    user_ids = {c.user_id for c in configs} | {t.user_id for t in tokens}
    max_failures = settings.calendar_sync_max_consecutive_failures

    synced = 0
    for user_id in user_ids:
        prefs = user_repo.get_preferences(user_id)
        if not _is_within_working_hours(prefs):
            continue

        user_configs = [c for c in configs if c.user_id == user_id]
        user_tokens = [t for t in tokens if t.user_id == user_id]
        max_err = max(
            (c.consecutive_error_count for c in user_configs),
            default=0,
        )
        max_err = max(
            max_err,
            max((t.consecutive_error_count for t in user_tokens), default=0),
        )
        if max_err >= max_failures:
            continue

        try:
            service.execute(user_id)
            synced += 1
        except Exception:
            logger.exception("Background sync failed for a user")

    if synced:
        logger.info("Background calendar sync: synced %d users", synced)
