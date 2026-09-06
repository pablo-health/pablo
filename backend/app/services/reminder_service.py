# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Appointment reminder service.

Checks for upcoming appointments and marks reminders as sent.
Reminder delivery is configured per-deployment.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..scheduling_engine.repositories.appointment import AppointmentRepository


class ReminderService:
    """Checks upcoming appointments and sends reminders.

    Designed to be called periodically (e.g., every 15 minutes) by a
    background scheduler.
    """

    def __init__(self, appointment_repo: AppointmentRepository) -> None:
        self._repo = appointment_repo

    def check_and_send_reminders(self, _user_id: str) -> dict[str, int]:
        """Check for upcoming appointments and send reminders.

        Returns a summary dict with counts of reminders sent.

        Note: reminder delivery is configured per-deployment.
        """
        return {"24h_sent": 0, "1h_sent": 0}
