# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Put a patient in a repository, in the test's own words."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from app.models import Patient


def seed_patient(
    repo: Any,
    user_id: str,
    *,
    patient_id: str | None = None,
    first_name: str = "Jane",
    last_name: str = "Doe",
    **fields: Any,
) -> Patient:
    """Create a patient owned by ``user_id`` in any repository with ``create``.

    ``fields`` are further ``Patient`` fields (``rate_cents``, ``diagnosis``,
    ``created_at`` ...).
    """
    now = datetime.now(UTC)
    patient = Patient(
        id=patient_id or str(uuid.uuid4()),
        first_name=first_name,
        last_name=last_name,
        **{"created_at": now, "updated_at": now, **fields},
    )
    created: Patient = repo.create(patient, user_id)
    return created
