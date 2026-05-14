# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""LLM-usage domain types (THERAPY-f6eg, Phase 3b of THERAPY-bhv).

See ``docs/architecture/patient-context-chat-oss.md`` §11.6. The meter
primitive lives in OSS so self-hosters get the same observability as
SaaS; quota *enforcement* is configured per deployment (OSS default
``llm_quota_enforcement=off``).

No ``tenant_id`` on these dataclasses — the practice schema is the
tenant boundary, matching :mod:`backend.app.models.chat`.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum


class QuotaStatus(StrEnum):
    """Result of :meth:`LlmUsageMeter.check_quota`."""

    OK = "ok"
    SOFT_WARN = "soft_warn"
    HARD_BLOCK = "hard_block"


@dataclass(frozen=True)
class UsageSummary:
    """Aggregated usage over a period.

    Returned by :meth:`LlmUsageMeter.get_period_usage`. Counts are
    summed across whichever ``(user_id, feature_key, model)`` slice
    the caller selected.
    """

    input_tokens: int
    output_tokens: int
    turn_count: int


@dataclass
class LlmUsageRecord:
    """One row in the monthly usage roll-up.

    The aggregation tuple is ``(user_id, feature_key, period_yyyymm,
    model)``. ``period_yyyymm`` is a 6-char ``YYYYMM`` string so the
    primary-key comparison stays exact-string and dialect-agnostic.
    """

    user_id: str
    feature_key: str
    period_yyyymm: str
    model: str
    input_tokens: int
    output_tokens: int
    turn_count: int
    first_recorded_at: datetime
    last_recorded_at: datetime
