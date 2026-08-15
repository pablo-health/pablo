# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Appointment type repository interface and in-memory implementation."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..models.appointment_type import AppointmentType


class AppointmentTypeRepository(ABC):
    """Abstract base class for appointment type data access."""

    @abstractmethod
    def get(self, appointment_type_id: str, user_id: str) -> AppointmentType | None:
        """Get an appointment type by ID, ensuring it belongs to the user."""

    @abstractmethod
    def list_by_user(self, user_id: str) -> list[AppointmentType]:
        """List all appointment types for a user."""

    @abstractmethod
    def create(self, appointment_type: AppointmentType) -> AppointmentType:
        """Create a new appointment type."""

    @abstractmethod
    def update(self, appointment_type: AppointmentType) -> AppointmentType:
        """Update an existing appointment type."""

    @abstractmethod
    def delete(self, appointment_type_id: str, user_id: str) -> bool:
        """Delete an appointment type. Returns True if deleted."""


class InMemoryAppointmentTypeRepository(AppointmentTypeRepository):
    """In-memory implementation for testing."""

    def __init__(self) -> None:
        self._types: dict[str, AppointmentType] = {}

    def get(self, appointment_type_id: str, user_id: str) -> AppointmentType | None:
        appointment_type = self._types.get(appointment_type_id)
        if appointment_type and appointment_type.user_id == user_id:
            return appointment_type
        return None

    def list_by_user(self, user_id: str) -> list[AppointmentType]:
        return [t for t in self._types.values() if t.user_id == user_id]

    def create(self, appointment_type: AppointmentType) -> AppointmentType:
        self._types[appointment_type.id] = appointment_type
        return appointment_type

    def update(self, appointment_type: AppointmentType) -> AppointmentType:
        self._types[appointment_type.id] = appointment_type
        return appointment_type

    def delete(self, appointment_type_id: str, user_id: str) -> bool:
        appointment_type = self.get(appointment_type_id, user_id)
        if not appointment_type:
            return False
        del self._types[appointment_type_id]
        return True
