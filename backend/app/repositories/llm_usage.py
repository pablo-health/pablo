# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""LLM usage rollup repository.

Used by ``LlmUsageMeter`` to record per-turn token usage and to answer
``check_quota`` without scanning the audit log. Rows are upserted on
each turn — there is no per-turn detail in this table.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class UsageSummary:
    event_count: int
    input_tokens: int
    output_tokens: int


class LlmUsageRepository(ABC):
    @abstractmethod
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
    ) -> None: ...

    @abstractmethod
    def summarize(
        self,
        *,
        tenant_id: str,
        period_yyyymm: int,
        feature_key: str | None = None,
    ) -> UsageSummary: ...


class InMemoryLlmUsageRepository(LlmUsageRepository):
    def __init__(self) -> None:
        self._rows: dict[tuple[str, str, str, int, str], UsageSummary] = {}

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
        key = (tenant_id, user_id, feature_key, period_yyyymm, model)
        existing = self._rows.get(key)
        if existing is None:
            self._rows[key] = UsageSummary(
                event_count=1,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
            )
        else:
            existing.event_count += 1
            existing.input_tokens += input_tokens
            existing.output_tokens += output_tokens

    def summarize(
        self,
        *,
        tenant_id: str,
        period_yyyymm: int,
        feature_key: str | None = None,
    ) -> UsageSummary:
        out = UsageSummary(0, 0, 0)
        for (t, _u, fk, p, _m), s in self._rows.items():
            if t != tenant_id or p != period_yyyymm:
                continue
            if feature_key is not None and fk != feature_key:
                continue
            out.event_count += s.event_count
            out.input_tokens += s.input_tokens
            out.output_tokens += s.output_tokens
        return out
