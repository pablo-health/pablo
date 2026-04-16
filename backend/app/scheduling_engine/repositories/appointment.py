# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Appointment repository interface and in-memory implementation."""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..models.appointment import Appointment


class AppointmentRepository(ABC):
    """Abstract base class for appointment data access."""

    @abstractmethod
    def get(self, appointment_id: str, user_id: str) -> Appointment | None:
        """Get appointment by ID, ensuring it belongs to the user."""

    @abstractmethod
    def list_by_range(
        self,
        user_id: str,
        start: str | datetime,
        end: str | datetime,
    ) -> list[Appointment]:
        """List appointments for a user within a date range."""

    @abstractmethod
    def list_by_patient(
        self,
        user_id: str,
        patient_id: str,
    ) -> list[Appointment]:
        """List appointments for a specific patient."""

    @abstractmethod
    def list_by_recurring_id(
        self,
        user_id: str,
        recurring_appointment_id: str,
        after: str | datetime | None = None,
    ) -> list[Appointment]:
        """List all occurrences of a recurring series, optionally after a date."""

    @abstractmethod
    def list_by_ical_source(
        self,
        user_id: str,
        ehr_system: str,
    ) -> list[Appointment]:
        """List all appointments synced from a specific iCal source."""

    @abstractmethod
    def create(self, appointment: Appointment) -> Appointment:
        """Create a new appointment."""

    @abstractmethod
    def create_batch(self, appointments: list[Appointment]) -> list[Appointment]:
        """Create multiple appointments in a batch."""

    @abstractmethod
    def update(self, appointment: Appointment) -> Appointment:
        """Update an existing appointment."""

    @abstractmethod
    def delete(self, appointment_id: str, user_id: str) -> bool:
        """Delete an appointment. Returns True if deleted."""


class InMemoryAppointmentRepository(AppointmentRepository):
    """In-memory implementation for testing."""

    def __init__(self) -> None:
        self._appointments: dict[str, Appointment] = {}

    def get(self, appointment_id: str, user_id: str) -> Appointment | None:
        appt = self._appointments.get(appointment_id)
        if appt and appt.user_id == user_id:
            return appt
        return None

    def list_by_range(
        self,
        user_id: str,
        start: str | datetime,
        end: str | datetime,
    ) -> list[Appointment]:
        start_dt = (
            start
            if isinstance(start, datetime)
            else datetime.fromisoformat(start.replace("Z", "+00:00"))
        )
        end_dt = (
            end if isinstance(end, datetime) else datetime.fromisoformat(end.replace("Z", "+00:00"))
        )
        return sorted(
            [
                a
                for a in self._appointments.values()
                if a.user_id == user_id and a.start_at >= start_dt and a.start_at < end_dt
            ],
            key=lambda a: a.start_at,
        )

    def list_by_patient(
        self,
        user_id: str,
        patient_id: str,
    ) -> list[Appointment]:
        return sorted(
            [
                a
                for a in self._appointments.values()
                if a.user_id == user_id and a.patient_id == patient_id
            ],
            key=lambda a: a.start_at,
        )

    def list_by_recurring_id(
        self,
        user_id: str,
        recurring_appointment_id: str,
        after: str | datetime | None = None,
    ) -> list[Appointment]:
        results = [
            a
            for a in self._appointments.values()
            if a.user_id == user_id and a.recurring_appointment_id == recurring_appointment_id
        ]
        if after:
            after_dt = (
                after
                if isinstance(after, datetime)
                else datetime.fromisoformat(after.replace("Z", "+00:00"))
            )
            results = [a for a in results if a.start_at >= after_dt]
        return sorted(results, key=lambda a: a.start_at)

    def list_by_ical_source(
        self,
        user_id: str,
        ehr_system: str,
    ) -> list[Appointment]:
        return sorted(
            [
                a
                for a in self._appointments.values()
                if a.user_id == user_id and a.ical_source == ehr_system
            ],
            key=lambda a: a.start_at,
        )

    def create(self, appointment: Appointment) -> Appointment:
        self._appointments[appointment.id] = appointment
        return appointment

    def create_batch(self, appointments: list[Appointment]) -> list[Appointment]:
        for appt in appointments:
            self._appointments[appt.id] = appt
        return appointments

    def update(self, appointment: Appointment) -> Appointment:
        self._appointments[appointment.id] = appointment
        return appointment

    def delete(self, appointment_id: str, user_id: str) -> bool:
        appt = self.get(appointment_id, user_id)
        if not appt:
            return False
        del self._appointments[appointment_id]
        return True
