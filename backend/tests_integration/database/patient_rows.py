# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""A patient row through the real repository, for tests that only need one to exist."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from app.repositories.postgres.patient import PostgresPatientRepository
from tests.patient_seeds import seed_patient

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


def seed_patient_row(session: Session, user_id: str) -> str:
    """Create a patient owned by ``user_id`` (the primary grant included); return its id."""
    created_at = datetime(2026, 1, 1, tzinfo=UTC)
    return seed_patient(
        PostgresPatientRepository(session),
        user_id,
        first_name="Pat",
        last_name="Ient",
        created_at=created_at,
        updated_at=created_at,
    ).id
