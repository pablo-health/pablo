# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Appointment reminder service.

Checks for upcoming appointments and marks reminders as sent.
Reminder delivery is configured per-deployment.

**What a reminder may say about an appointment lives here**, even though the
sending does not. A reminder carries the time and the practice's name: it is
read on a lock screen, forwarded, and seen by whoever is holding the phone,
so the less it says about why the person has an appointment the better.

The join link is the one thing a message adds to that, for a video
appointment. It goes out by default; a practice turns it off in Settings, and
a deployment can turn it off for everyone. Whether it goes is decided in
:func:`app.services.telehealth.client_join_link`, which every message to a
client asks, so a reminder and a booking message cannot answer it
differently.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .telehealth import client_join_link

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
    def reminder_fields(appointment: Appointment, *, practice_sends_link: bool) -> dict[str, str]:
        """What a dispatcher may put in this appointment's reminder.

        ``practice_sends_link`` is the practice's own answer, from
        :func:`app.services.telehealth.practice_sends_client_video_link`. The
        link is the only field, and only for a video appointment that has one.
        Never carries a name, a reason, or anything else off the chart.
        """
        link = client_join_link(appointment, practice_sends_link=practice_sends_link)
        return {"join_url": link} if link else {}
