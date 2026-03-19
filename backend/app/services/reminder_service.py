# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Appointment reminder service.

Checks for upcoming appointments and marks reminders as sent.
Actual email delivery is a future integration — currently logs reminders.

HIPAA: No PHI (patient names, session details) in log messages.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..scheduling_engine.repositories.appointment import AppointmentRepository

logger = logging.getLogger(__name__)


class ReminderService:
    """Checks upcoming appointments and sends reminders.

    Designed to be called periodically (e.g., every 15 minutes) by a
    background scheduler. For now, reminders are logged; actual email
    sending will be added when the notification service is built.
    """

    def __init__(self, appointment_repo: AppointmentRepository) -> None:
        self._repo = appointment_repo

    def check_and_send_reminders(self, user_id: str) -> dict[str, int]:
        """Check for upcoming appointments and send reminders.

        Returns a summary dict with counts of reminders sent.
        """
        now = datetime.now(UTC)
        sent_24h = 0
        sent_1h = 0

        # 24-hour reminders: appointments between 23h and 25h from now
        window_24h_start = (now + timedelta(hours=23)).isoformat().replace("+00:00", "Z")
        window_24h_end = (now + timedelta(hours=25)).isoformat().replace("+00:00", "Z")
        appointments_24h = self._repo.list_by_range(user_id, window_24h_start, window_24h_end)

        for appt in appointments_24h:
            if appt.status == "cancelled":
                continue
            if not appt.reminder_24h_sent:
                self._send_24h_reminder(appt.id, appt.patient_id)
                appt.reminder_24h_sent = True
                self._repo.update(appt)
                sent_24h += 1

        # 1-hour reminders: appointments between 30min and 90min from now
        window_1h_start = (now + timedelta(minutes=30)).isoformat().replace("+00:00", "Z")
        window_1h_end = (now + timedelta(minutes=90)).isoformat().replace("+00:00", "Z")
        appointments_1h = self._repo.list_by_range(user_id, window_1h_start, window_1h_end)

        for appt in appointments_1h:
            if appt.status == "cancelled":
                continue
            if not appt.reminder_1h_sent:
                self._send_1h_reminder(appt.id, appt.patient_id)
                appt.reminder_1h_sent = True
                self._repo.update(appt)
                sent_1h += 1

        # HIPAA: log counts only, no patient details
        if sent_24h or sent_1h:
            logger.info(
                "Reminders sent: 24h=%d, 1h=%d",
                sent_24h,
                sent_1h,
            )

        return {"24h_sent": sent_24h, "1h_sent": sent_1h}

    @staticmethod
    def _send_24h_reminder(appointment_id: str, _patient_id: str) -> None:
        """Send a 24-hour reminder for an appointment.

        TODO: Integrate with email/notification service.
        Currently logs the reminder action without PHI.
        """
        # HIPAA: log IDs only, never patient names or session content
        logger.info("24h reminder queued for appointment %s", appointment_id)

    @staticmethod
    def _send_1h_reminder(appointment_id: str, _patient_id: str) -> None:
        """Send a 1-hour reminder for an appointment.

        TODO: Integrate with email/notification service.
        Currently logs the reminder action without PHI.
        """
        # HIPAA: log IDs only, never patient names or session content
        logger.info("1h reminder queued for appointment %s", appointment_id)
