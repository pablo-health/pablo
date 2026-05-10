# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""LLM usage metering and quota primitive.

The meter records every LLM-billable event (chat turns today; SOAP
generation when it opts in) and answers ``check_quota`` so the chat
turn endpoint can short-circuit when a tenant has exhausted its
allowance.

Defaults:

* ``LLM_QUOTA_ENFORCEMENT=False`` — record-only mode. Self-host
  default; the meter still reports usage but never blocks a turn.
* ``LLM_QUOTA_ENFORCEMENT=True`` — quotas resolved from the optional
  ``llm_quota`` block in tenant config (TenantQuotaConfig). Missing
  block ⇒ unlimited.

The ``user_id`` parameter on ``check_quota`` is accepted for forward
compatibility (per-user sub-quotas inside a tenant pool) but ignored
in v1: in single-clinician deployments tenant ≈ user and the tenant
quota is the only one that matters.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, Literal

from ..settings import get_settings

if TYPE_CHECKING:
    from datetime import datetime

    from ..repositories.llm_usage import LlmUsageRepository, UsageSummary


class QuotaStatus(StrEnum):
    OK = "ok"
    SOFT_WARN = "soft_warn"
    HARD_BLOCK = "hard_block"


@dataclass(frozen=True)
class TenantQuotaConfig:
    """Per-tenant quota config — usually loaded from tenant_settings.

    Counting is by event, not raw tokens, because that's the unit a
    clinician can reason about ("conversations", "notes",
    "justifications"). Token-count limits are still configurable as a
    backstop but default to None (off).
    """

    monthly_chat_conversations: int | None = None
    monthly_soap_notes: int | None = None
    monthly_justifications: int | None = None
    monthly_tokens_input: int | None = None
    monthly_tokens_output: int | None = None
    soft_warn_pct: int = 80
    hard_block_on_exceed: bool = True


@dataclass(frozen=True)
class QuotaCheckResult:
    status: QuotaStatus
    quota_remaining_pct: int | None = None
    limit: int | None = None
    used: int | None = None
    feature_key: str | None = None


# Map a caller_feature_key to the matching limit field on TenantQuotaConfig.
# Unmapped feature keys are uncounted against any cap.
_FEATURE_LIMIT_FIELD: dict[str, str] = {
    "chart_qa": "monthly_chat_conversations",
    "rx_justification_workspace": "monthly_justifications",
    "soap_generation": "monthly_soap_notes",
}


class LlmUsageMeter:
    def __init__(
        self,
        repo: LlmUsageRepository,
        *,
        quota_config: TenantQuotaConfig | None = None,
    ) -> None:
        self._repo = repo
        self._quota_config = quota_config or TenantQuotaConfig()

    @staticmethod
    def _period_for(now: datetime | None = None) -> int:
        from ..utcnow import utc_now

        d = now or utc_now()
        return d.year * 100 + d.month

    def record_turn(
        self,
        *,
        tenant_id: str,
        user_id: str,
        feature_key: str,
        model: str,
        input_tokens: int,
        output_tokens: int,
        now: datetime | None = None,
    ) -> None:
        self._repo.record(
            tenant_id=tenant_id,
            user_id=user_id,
            feature_key=feature_key,
            period_yyyymm=self._period_for(now),
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        )

    def get_period_usage(
        self,
        *,
        tenant_id: str,
        feature_key: str | None = None,
        now: datetime | None = None,
    ) -> UsageSummary:
        return self._repo.summarize(
            tenant_id=tenant_id,
            period_yyyymm=self._period_for(now),
            feature_key=feature_key,
        )

    def check_quota(
        self,
        *,
        tenant_id: str,
        user_id: str,  # noqa: ARG002 — reserved for per-user sub-quotas
        feature_key: str,
        now: datetime | None = None,
    ) -> QuotaCheckResult:
        """Return the gate decision for the next event of ``feature_key``."""
        if not get_settings().llm_quota_enforcement:
            return QuotaCheckResult(status=QuotaStatus.OK, feature_key=feature_key)

        limit_field: Literal[
            "monthly_chat_conversations",
            "monthly_soap_notes",
            "monthly_justifications",
        ] | None = _FEATURE_LIMIT_FIELD.get(feature_key)  # type: ignore[assignment]
        if limit_field is None:
            return QuotaCheckResult(status=QuotaStatus.OK, feature_key=feature_key)

        limit: int | None = getattr(self._quota_config, limit_field)
        if limit is None or limit <= 0:
            return QuotaCheckResult(status=QuotaStatus.OK, feature_key=feature_key)

        usage = self.get_period_usage(
            tenant_id=tenant_id, feature_key=feature_key, now=now
        )
        used = usage.event_count
        if used >= limit and self._quota_config.hard_block_on_exceed:
            return QuotaCheckResult(
                status=QuotaStatus.HARD_BLOCK,
                quota_remaining_pct=0,
                limit=limit,
                used=used,
                feature_key=feature_key,
            )
        pct_used = int(used * 100 / limit) if limit else 0
        if pct_used >= self._quota_config.soft_warn_pct:
            return QuotaCheckResult(
                status=QuotaStatus.SOFT_WARN,
                quota_remaining_pct=max(0, 100 - pct_used),
                limit=limit,
                used=used,
                feature_key=feature_key,
            )
        return QuotaCheckResult(
            status=QuotaStatus.OK,
            quota_remaining_pct=max(0, 100 - pct_used),
            limit=limit,
            used=used,
            feature_key=feature_key,
        )


def get_llm_usage_meter() -> LlmUsageMeter:
    """FastAPI dependency — request-scoped meter backed by the DB repo."""
    try:
        from ..repositories import get_llm_usage_repository

        return LlmUsageMeter(get_llm_usage_repository())
    except RuntimeError:
        from ..repositories.llm_usage import InMemoryLlmUsageRepository

        return LlmUsageMeter(InMemoryLlmUsageRepository())
