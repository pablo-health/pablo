# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""PostgreSQL EHR prompt repository implementation."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ...db.models import EhrPromptRow
from ...models.ehr_prompt import EhrPrompt
from ...utcnow import utc_now
from ..ehr_prompt import EhrPromptRepository

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


class PostgresEhrPromptRepository(EhrPromptRepository):
    def __init__(self, session: Session) -> None:
        self._session = session

    def get(self, ehr_system: str) -> EhrPrompt | None:
        row = self._session.get(EhrPromptRow, ehr_system)
        if row is None:
            return None
        return _row_to_prompt(row)

    def upsert(self, prompt: EhrPrompt) -> EhrPrompt:
        now = utc_now()
        prompt.updated_at = now
        row = self._session.get(EhrPromptRow, prompt.ehr_system)
        if row is None:
            row = EhrPromptRow(ehr_system=prompt.ehr_system)
            self._session.add(row)
        row.system_prompt = prompt.system_prompt
        row.version = prompt.version
        row.updated_at = prompt.updated_at
        row.updated_by = prompt.updated_by
        row.notes = prompt.notes
        self._session.flush()
        return prompt


def _row_to_prompt(row: EhrPromptRow) -> EhrPrompt:
    return EhrPrompt(
        ehr_system=row.ehr_system,
        system_prompt=row.system_prompt,
        version=row.version,
        updated_at=row.updated_at,
        updated_by=row.updated_by,
        notes=row.notes,
    )
