# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Firestore appointment repository implementation."""

from __future__ import annotations

from typing import Any

from ..scheduling_engine.models.appointment import Appointment
from ..scheduling_engine.repositories.appointment import AppointmentRepository


class FirestoreAppointmentRepository(AppointmentRepository):
    """Firestore implementation of AppointmentRepository."""

    def __init__(self, db: Any) -> None:
        self.db = db
        self.collection = db.collection("appointments")

    def get(self, appointment_id: str, user_id: str) -> Appointment | None:
        doc = self.collection.document(appointment_id).get()
        if doc.exists:
            appt = Appointment.from_dict(doc.to_dict())
            if appt.user_id == user_id:
                return appt
        return None

    def list_by_range(
        self,
        user_id: str,
        start: str,
        end: str,
    ) -> list[Appointment]:
        query = (
            self.collection.where("user_id", "==", user_id)
            .where("start_at", ">=", start)
            .where("start_at", "<", end)
            .order_by("start_at")
        )
        return [Appointment.from_dict(doc.to_dict()) for doc in query.stream()]

    def list_by_patient(
        self,
        user_id: str,
        patient_id: str,
    ) -> list[Appointment]:
        query = (
            self.collection.where("user_id", "==", user_id)
            .where("patient_id", "==", patient_id)
            .order_by("start_at")
        )
        return [Appointment.from_dict(doc.to_dict()) for doc in query.stream()]

    def list_by_recurring_id(
        self,
        user_id: str,
        recurring_appointment_id: str,
        after: str | None = None,
    ) -> list[Appointment]:
        query = self.collection.where("user_id", "==", user_id).where(
            "recurring_appointment_id", "==", recurring_appointment_id
        )
        if after:
            query = query.where("start_at", ">=", after)
        query = query.order_by("start_at")
        return [Appointment.from_dict(doc.to_dict()) for doc in query.stream()]

    def list_by_ical_source(
        self,
        user_id: str,
        ehr_system: str,
    ) -> list[Appointment]:
        query = (
            self.collection.where("user_id", "==", user_id)
            .where("ical_source", "==", ehr_system)
            .order_by("start_at")
        )
        return [Appointment.from_dict(doc.to_dict()) for doc in query.stream()]

    def create(self, appointment: Appointment) -> Appointment:
        self.collection.document(appointment.id).set(appointment.to_dict())
        return appointment

    def create_batch(self, appointments: list[Appointment]) -> list[Appointment]:
        batch = self.db.batch()
        for appt in appointments:
            ref = self.collection.document(appt.id)
            batch.set(ref, appt.to_dict())
        batch.commit()
        return appointments

    def update(self, appointment: Appointment) -> Appointment:
        self.collection.document(appointment.id).set(appointment.to_dict())
        return appointment

    def delete(self, appointment_id: str, user_id: str) -> bool:
        appt = self.get(appointment_id, user_id)
        if not appt:
            return False
        self.collection.document(appointment_id).delete()
        return True
