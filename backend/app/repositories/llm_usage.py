# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""LLM-usage repository contract (THERAPY-f6eg, Phase 3b of THERAPY-bhv).

Two operations only: upsert one turn's counts into the monthly bucket,
and read summed usage over a period. The meter (§11.6) is the only
caller; downstream consumers may layer tier-aware quota config on top
without needing their own repository.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

from ..models import LlmUsageRecord, UsageSummary

if TYPE_CHECKING:
    from datetime import datetime


class LlmUsageRepository(ABC):
    """Abstract base for LLM usage aggregate storage."""

    @abstractmethod
    def record_turn(  # noqa: PLR0913 — aggregate key + counts
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
        """Increment the monthly bucket by one turn's counts.

        Inserts a new row when no bucket exists for the aggregation
        tuple; otherwise sums into the existing row. Idempotency is
        the caller's responsibility — the meter calls this exactly
        once per successful turn.
        """

    @abstractmethod
    def get_period_usage(
        self,
        *,
        period_start_yyyymm: str,
        period_end_yyyymm: str,
        user_id: str | None = None,
        feature_key: str | None = None,
    ) -> UsageSummary:
        """Return summed usage over an inclusive month range.

        ``period_start_yyyymm`` and ``period_end_yyyymm`` are 6-char
        ``YYYYMM`` strings; comparison is string-exact and works the
        same in Postgres and the in-memory backend.
        """

    @abstractmethod
    def list_records(
        self,
        *,
        period_yyyymm: str,
    ) -> list[LlmUsageRecord]:
        """Return all rows in a given month (debug / admin surface)."""


class InMemoryLlmUsageRepository(LlmUsageRepository):
    """In-memory ``LlmUsageRepository`` for unit tests."""

    def __init__(self) -> None:
        # String keys avoid the runtime import the dataclass would
        # require — TYPE_CHECKING above keeps the names available for
        # static analysis.
        self._rows: dict[tuple[str, str, str, str], LlmUsageRecord] = {}

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
        key = (user_id, feature_key, period_yyyymm, model)
        existing = self._rows.get(key)
        if existing is None:
            self._rows[key] = LlmUsageRecord(
                user_id=user_id,
                feature_key=feature_key,
                period_yyyymm=period_yyyymm,
                model=model,
                input_tokens=max(0, input_tokens),
                output_tokens=max(0, output_tokens),
                turn_count=1,
                first_recorded_at=recorded_at,
                last_recorded_at=recorded_at,
            )
            return
        existing.input_tokens += max(0, input_tokens)
        existing.output_tokens += max(0, output_tokens)
        existing.turn_count += 1
        existing.last_recorded_at = recorded_at

    def get_period_usage(
        self,
        *,
        period_start_yyyymm: str,
        period_end_yyyymm: str,
        user_id: str | None = None,
        feature_key: str | None = None,
    ) -> UsageSummary:
        input_tokens = 0
        output_tokens = 0
        turn_count = 0
        for row in self._rows.values():
            if not (period_start_yyyymm <= row.period_yyyymm <= period_end_yyyymm):
                continue
            if user_id is not None and row.user_id != user_id:
                continue
            if feature_key is not None and row.feature_key != feature_key:
                continue
            input_tokens += row.input_tokens
            output_tokens += row.output_tokens
            turn_count += row.turn_count
        return UsageSummary(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            turn_count=turn_count,
        )

    def list_records(
        self,
        *,
        period_yyyymm: str,
    ) -> list[LlmUsageRecord]:
        return [r for r in self._rows.values() if r.period_yyyymm == period_yyyymm]
