# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Patient-preference operations.

Every write is checked against its kind (``kinds.normalize``). A preference is
never edited away: retiring it keeps the row, with who retired it and when, so
the chart's record of what was known stays whole. Rows go only when the
client's record does.
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from ..utcnow import utc_now
from .kinds import get_kind, normalize
from .models import Preference

if TYPE_CHECKING:
    from collections.abc import Collection

    from ..repositories.patient_preference import PatientPreferenceRepository
    from .schemas import AddPreferenceRequest, UpdatePreferenceRequest


class PreferenceNotFoundError(LookupError):
    """No active preference with that id for this patient."""


class DuplicatePreferenceError(ValueError):
    """The kind allows one active row per client and there is one: ``existing``."""

    def __init__(self, existing: Preference) -> None:
        super().__init__("an active preference of this kind exists")
        self.existing = existing


class PreferenceService:
    def __init__(self, repo: PatientPreferenceRepository) -> None:
        self._repo = repo

    def preferences(self, patient_id: str, *, include_retired: bool = False) -> list[Preference]:
        return self._repo.list_by_patient(patient_id, include_retired=include_retired)

    def active_for_patients(
        self, patient_ids: Collection[str], kinds: Collection[str]
    ) -> list[Preference]:
        """Active rows of the given kinds for many clients, in one read."""
        if not patient_ids or not kinds:
            return []
        return self._repo.list_active_for_patients(patient_ids, kinds)

    def add(self, patient_id: str, user_id: str, body: AddPreferenceRequest) -> Preference:
        n = normalize(body.kind, body.value, body.text, body.strength)
        spec = get_kind(body.kind)
        if spec is not None and spec.single_active:
            existing = self._repo.list_active(patient_id, [body.kind])
            if existing:
                raise DuplicatePreferenceError(existing[0])
        now = utc_now()
        return self._repo.save(
            Preference(
                id=str(uuid.uuid4()),
                patient_id=patient_id,
                domain=n.domain.value,
                kind=body.kind,
                value=n.value,
                text=n.text,
                strength=n.strength.value if n.strength else None,
                source=body.source,
                source_ref=body.source_ref,
                stated_at=now,
                created_at=now,
                updated_at=now,
                created_by=user_id,
            )
        )

    def update(
        self, patient_id: str, preference_id: str, body: UpdatePreferenceRequest
    ) -> Preference:
        """Restate a preference. It reads as said now, by the clinician."""
        current = self._active(patient_id, preference_id)
        sent = body.model_fields_set
        n = normalize(
            current.kind,
            body.value if "value" in sent else current.value,
            body.text if "text" in sent else current.text,
            body.strength if "strength" in sent else current.strength,
        )
        now = utc_now()
        current.value = n.value
        current.text = n.text
        current.strength = n.strength.value if n.strength else None
        current.stated_at = now
        current.confirmed_at = now
        current.updated_at = now
        return self._repo.save(current)

    def retire(self, patient_id: str, preference_id: str, user_id: str) -> Preference:
        current = self._active(patient_id, preference_id)
        now = utc_now()
        current.retired_at = now
        current.retired_by = user_id
        current.updated_at = now
        return self._repo.save(current)

    def _active(self, patient_id: str, preference_id: str) -> Preference:
        current = self._repo.get(patient_id, preference_id)
        if current is None or current.retired_at is not None:
            raise PreferenceNotFoundError(preference_id)
        return current
