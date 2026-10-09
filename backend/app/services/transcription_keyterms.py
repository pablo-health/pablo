# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""A session's transcription vocabulary, read from its client's chart.

The medication list in every status (current first, then stopped, as the
record orders it) and the allergy substances, handed to
:func:`app.drug_names.keyterms.chart_keyterms`. Read inside the submit
worker's tenant-scoped session, as the clinician who recorded it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..drug_names.keyterms import chart_keyterms
from ..repositories.postgres.medication import PostgresMedicationRepository
from ..repositories.postgres.patient import PostgresPatientRepository

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


def session_keyterms(db: Session, patient_id: str, user_id: str) -> list[str]:
    """The vocabulary for a session with ``patient_id``; empty when the chart names nothing."""
    patient = PostgresPatientRepository(db).get(patient_id, user_id)
    if patient is None:
        return []
    names = [
        str(row["drug_name"])
        for row in PostgresMedicationRepository(db).list_by_patient(patient_id, user_id)
    ]
    names.extend(entry.get("substance") or "" for entry in patient.allergies)
    return chart_keyterms(names)
