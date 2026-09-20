# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Appointment reminder service.

Checks for upcoming appointments and marks reminders as sent.
Reminder delivery is configured per-deployment.

**What a reminder may say about an appointment lives here**, even though the
sending does not. A reminder carries the time and the practice's name: it is
read on a lock screen, forwarded, and seen by whoever is holding the phone,
so the less it says about why the person has an appointment the better.

The join link is the one thing a practice may add to that, and it is off by
default. It is not neutral — it names the video service, and it says an
appointment exists — so the decision belongs to the practice rather than to
whichever dispatcher happens to build the message. :meth:`reminder_fields` is
where that decision is applied, once, so two dispatchers cannot answer it
differently.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..settings import get_settings
from .telehealth import reminder_join_link

if TYPE_CHECKING:
    from ..scheduling_engine.models.appointment import Appointment
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

    @staticmethod
    def reminder_fields(appointment: Appointment) -> dict[str, str]:
        """What a dispatcher may put in this appointment's reminder.

        Empty when the practice has not turned the join link on, which is the
        default — so a dispatcher that merges this into its message says
        exactly what it says today until somebody decides otherwise. Never
        carries a name, a reason, or anything else off the chart.
        """
        link = reminder_join_link(
            appointment,
            include_join_link=get_settings().telehealth_include_join_link_in_reminders,
        )
        return {"join_url": link} if link else {}
