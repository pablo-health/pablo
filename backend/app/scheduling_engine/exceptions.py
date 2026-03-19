# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Scheduling engine exceptions."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .models.conflict import Conflict


class SchedulingError(Exception):
    """Base exception for scheduling engine."""


class AppointmentNotFoundError(SchedulingError):
    """Raised when an appointment is not found."""

    def __init__(self, appointment_id: str) -> None:
        self.appointment_id = appointment_id
        super().__init__(f"Appointment not found: {appointment_id}")


class AppointmentConflictError(SchedulingError):
    """Raised when an appointment conflicts with existing appointments or rules."""

    def __init__(self, message: str, conflicts: list[Conflict] | None = None) -> None:
        self.conflicts = conflicts or []
        super().__init__(message)


class InvalidAppointmentError(SchedulingError):
    """Raised when appointment data is invalid."""


class InvalidRecurrenceError(SchedulingError):
    """Raised when recurrence parameters are invalid."""
