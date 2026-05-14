# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""PostgreSQL ``LlmUsageRepository`` (THERAPY-f6eg)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import and_, func, select
from sqlalchemy.dialects.postgresql import insert

from ...db.models import LlmUsageRow
from ...models import LlmUsageRecord, UsageSummary
from ..llm_usage import LlmUsageRepository

if TYPE_CHECKING:
    from datetime import datetime

    from sqlalchemy.orm import Session


def _row_to_record(row: LlmUsageRow) -> LlmUsageRecord:
    return LlmUsageRecord(
        user_id=row.user_id,
        feature_key=row.feature_key,
        period_yyyymm=row.period_yyyymm,
        model=row.model,
        input_tokens=row.input_tokens,
        output_tokens=row.output_tokens,
        turn_count=row.turn_count,
        first_recorded_at=row.first_recorded_at,
        last_recorded_at=row.last_recorded_at,
    )


class PostgresLlmUsageRepository(LlmUsageRepository):
    def __init__(self, session: Session) -> None:
        self._session = session

    def record_turn(  # noqa: PLR0913
        self,
        *,
        user_id: str,
        feature_key: str,
        period_yyyymm: str,
        model: str,
        input_tokens: int,
        output_tokens: int,
        recorded_at: datetime,
    ) -> None:
        input_delta = max(0, input_tokens)
        output_delta = max(0, output_tokens)
        stmt = insert(LlmUsageRow).values(
            user_id=user_id,
            feature_key=feature_key,
            period_yyyymm=period_yyyymm,
            model=model,
            input_tokens=input_delta,
            output_tokens=output_delta,
            turn_count=1,
            first_recorded_at=recorded_at,
            last_recorded_at=recorded_at,
        )
        # Atomic upsert: increment counters when the aggregate row
        # already exists. ``first_recorded_at`` keeps its original
        # value across conflicts; ``last_recorded_at`` rolls forward.
        stmt = stmt.on_conflict_do_update(
            constraint="pk_llm_usage",
            set_={
                "input_tokens": LlmUsageRow.input_tokens + input_delta,
                "output_tokens": LlmUsageRow.output_tokens + output_delta,
                "turn_count": LlmUsageRow.turn_count + 1,
                "last_recorded_at": recorded_at,
            },
        )
        self._session.execute(stmt)
        self._session.flush()

    def get_period_usage(
        self,
        *,
        period_start_yyyymm: str,
        period_end_yyyymm: str,
        user_id: str | None = None,
        feature_key: str | None = None,
    ) -> UsageSummary:
        clauses = [
            LlmUsageRow.period_yyyymm >= period_start_yyyymm,
            LlmUsageRow.period_yyyymm <= period_end_yyyymm,
        ]
        if user_id is not None:
            clauses.append(LlmUsageRow.user_id == user_id)
        if feature_key is not None:
            clauses.append(LlmUsageRow.feature_key == feature_key)

        stmt = select(
            func.coalesce(func.sum(LlmUsageRow.input_tokens), 0),
            func.coalesce(func.sum(LlmUsageRow.output_tokens), 0),
            func.coalesce(func.sum(LlmUsageRow.turn_count), 0),
        ).where(and_(*clauses))
        input_total, output_total, turn_total = self._session.execute(stmt).one()
        return UsageSummary(
            input_tokens=int(input_total),
            output_tokens=int(output_total),
            turn_count=int(turn_total),
        )

    def list_records(
        self,
        *,
        period_yyyymm: str,
    ) -> list[LlmUsageRecord]:
        stmt = select(LlmUsageRow).where(LlmUsageRow.period_yyyymm == period_yyyymm)
        rows = self._session.execute(stmt).scalars().all()
        return [_row_to_record(r) for r in rows]
