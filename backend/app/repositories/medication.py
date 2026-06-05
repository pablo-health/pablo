# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Re-export of the medication repository interface.

The abstract class and InMemoryMedicationRepository live in
``app.medications.repository`` (co-located with the module they serve).
This shim lets ``app.repositories.postgres.medication`` follow the same
import pattern as every other postgres repository
(``from ..medication import ...``).
"""

from __future__ import annotations

from ..medications.repository import (  # noqa: F401
    InMemoryMedicationRepository,
    MedicationRepository,
    PatientMedicationAccessDeniedError,
)
