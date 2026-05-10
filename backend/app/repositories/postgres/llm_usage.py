# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""PostgreSQL implementation of the LLM usage rollup repository."""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from ...db.models import LlmUsageRow
from ...utcnow import utc_now
from ..llm_usage import LlmUsageRepository, UsageSummary

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


class PostgresLlmUsageRepository(LlmUsageRepository):
    def __init__(self, session: Session) -> None:
        self._session = session

    def record(  # noqa: PLR0913 — usage rollup primary key has 5 components
        self,
        *,
        tenant_id: str,
        user_id: str,
        feature_key: str,
        period_yyyymm: int,
        model: str,
        input_tokens: int,
        output_tokens: int,
    ) -> None:
        now = utc_now()
        stmt = pg_insert(LlmUsageRow).values(
            tenant_id=tenant_id,
            user_id=user_id,
            feature_key=feature_key,
            period_yyyymm=period_yyyymm,
            model=model,
            event_count=1,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            updated_at=now,
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=[
                "tenant_id",
                "user_id",
                "feature_key",
                "period_yyyymm",
                "model",
            ],
            set_={
                "event_count": LlmUsageRow.event_count + 1,
                "input_tokens": LlmUsageRow.input_tokens + input_tokens,
                "output_tokens": LlmUsageRow.output_tokens + output_tokens,
                "updated_at": now,
            },
        )
        self._session.execute(stmt)
        self._session.flush()

    def summarize(
        self,
        *,
        tenant_id: str,
        period_yyyymm: int,
        feature_key: str | None = None,
    ) -> UsageSummary:
        query = select(
            func.coalesce(func.sum(LlmUsageRow.event_count), 0),
            func.coalesce(func.sum(LlmUsageRow.input_tokens), 0),
            func.coalesce(func.sum(LlmUsageRow.output_tokens), 0),
        ).where(
            LlmUsageRow.tenant_id == tenant_id,
            LlmUsageRow.period_yyyymm == period_yyyymm,
        )
        if feature_key is not None:
            query = query.where(LlmUsageRow.feature_key == feature_key)
        row = self._session.execute(query).one()
        return UsageSummary(
            event_count=int(row[0]),
            input_tokens=int(row[1]),
            output_tokens=int(row[2]),
        )
