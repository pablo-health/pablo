# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Lookups that several routers share: the caller's practice, and a patient
the caller may act on.

Each one raises the error its routers already answer with, so a route that
moves onto these keeps its status code and its words.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import HTTPException, status

from ..api_errors import ConflictError, NotFoundError
from ..auth.service import _resolve_practice_from_email
from .users import _resolve_practice_id_for

if TYPE_CHECKING:
    from collections.abc import Callable

    from ..models import User
    from ..models.patient import Patient
    from ..repositories.patient import PatientRepository


def practice_id_or_409(user: User) -> str:
    """The caller's practice, or 409 when no practice is associated with them yet."""
    practice = _resolve_practice_from_email(user.email)
    if practice is None:
        raise ConflictError("No practice is associated with this account yet.")
    return practice[0]


def practice_id_or_404(user: User) -> str:
    """The caller's practice, or 404 ``NO_PRACTICE`` when there is no mapping."""
    practice_id = _resolve_practice_id_for(user)
    if practice_id is None:
        raise NotFoundError("No practice mapping for this account", code="NO_PRACTICE")
    return practice_id


def client_not_found(_patient_id: str) -> Exception:
    """404 ``Client not found.``, the billing routes' answer."""
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Client not found.")


def patient_not_found(patient_id: str) -> Exception:
    """404 ``Patient not found`` naming the id, the chart routes' answer."""
    return NotFoundError("Patient not found", {"patient_id": patient_id})


def visible_patient(
    patients: PatientRepository,
    patient_id: str,
    user_id: str,
    not_found: Callable[[str], Exception],
) -> Patient:
    """The patient this clinician may act on, or ``not_found(patient_id)`` raised.

    Through the repository rather than a direct row read, so the clinician's
    access grant decides. A client in another practice's schema is absent from
    the tenant-scoped repository; one this clinician holds no grant on is
    filtered out by the row policy. Both collapse to "not found" on purpose: a
    403 would confirm the id exists.
    """
    patient = patients.get(patient_id, user_id)
    if patient is None:
        raise not_found(patient_id)
    return patient
