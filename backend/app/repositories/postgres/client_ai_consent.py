# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""PostgreSQL client AI-notes consent repository.

The table's row policy is ``has_patient_access``, so a clinician without a
grant on the client reads nothing and cannot write.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

from sqlalchemy import select

from ...db.models import ClientAiConsentEventRow
from ...db.platform_models import PlatformUserRow
from ...models.client_ai_consent import AiConsentDecision, AiConsentEvent, AiConsentSource
from ..client_ai_consent import ClientAiConsentRepository

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


def _to_event(row: ClientAiConsentEventRow, recorded_by_name: str | None) -> AiConsentEvent:
    return AiConsentEvent(
        id=str(row.id),
        patient_id=str(row.patient_id),
        decision=cast("AiConsentDecision", row.decision),
        effective_on=row.effective_on,
        source=cast("AiConsentSource", row.source),
        recorded_by=str(row.recorded_by) if row.recorded_by else None,
        recorded_by_name=(recorded_by_name or "").strip() or None,
        recorded_at=row.recorded_at,
        intake_submission_id=str(row.intake_submission_id) if row.intake_submission_id else None,
    )


class PostgresClientAiConsentRepository(ClientAiConsentRepository):
    def __init__(self, session: Session) -> None:
        self._session = session

    def append(self, event: AiConsentEvent) -> AiConsentEvent:
        self._session.add(
            ClientAiConsentEventRow(
                id=event.id,
                patient_id=event.patient_id,
                decision=event.decision,
                effective_on=event.effective_on,
                source=event.source,
                recorded_by=event.recorded_by,
                recorded_at=event.recorded_at,
                intake_submission_id=event.intake_submission_id,
            )
        )
        self._session.flush()
        return event

    def list_for_patient(self, patient_id: str) -> list[AiConsentEvent]:
        rows = self._session.execute(
            select(ClientAiConsentEventRow, PlatformUserRow.name)
            .outerjoin(PlatformUserRow, PlatformUserRow.id == ClientAiConsentEventRow.recorded_by)
            .where(ClientAiConsentEventRow.patient_id == patient_id)
            .order_by(ClientAiConsentEventRow.recorded_at, ClientAiConsentEventRow.id)
        ).all()
        return [_to_event(row, name) for row, name in rows]
