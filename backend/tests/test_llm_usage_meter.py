# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Tests for the LLM usage meter primitive."""

import os
from datetime import UTC, datetime
from unittest.mock import patch

import pytest
from app.repositories.llm_usage import InMemoryLlmUsageRepository
from app.services.llm_usage_meter import (
    LlmUsageMeter,
    QuotaStatus,
    TenantQuotaConfig,
)
from app.settings import get_settings


@pytest.fixture
def now() -> datetime:
    return datetime(2026, 5, 1, tzinfo=UTC)


def test_record_and_summarize_rolls_up_per_period(now: datetime) -> None:
    meter = LlmUsageMeter(InMemoryLlmUsageRepository())
    meter.record_turn(
        tenant_id="t1",
        user_id="u1",
        feature_key="chart_qa",
        model="m",
        input_tokens=100,
        output_tokens=20,
        now=now,
    )
    meter.record_turn(
        tenant_id="t1",
        user_id="u1",
        feature_key="chart_qa",
        model="m",
        input_tokens=200,
        output_tokens=40,
        now=now,
    )
    summary = meter.get_period_usage(
        tenant_id="t1", feature_key="chart_qa", now=now
    )
    assert summary.event_count == 2
    assert summary.input_tokens == 300
    assert summary.output_tokens == 60


def test_check_quota_disabled_by_default_returns_ok(now: datetime) -> None:
    meter = LlmUsageMeter(InMemoryLlmUsageRepository())
    result = meter.check_quota(
        tenant_id="t1", user_id="u1", feature_key="chart_qa", now=now
    )
    assert result.status == QuotaStatus.OK


def test_check_quota_hard_blocks_when_enforcement_enabled(now: datetime) -> None:
    meter = LlmUsageMeter(
        InMemoryLlmUsageRepository(),
        quota_config=TenantQuotaConfig(monthly_chat_conversations=2),
    )
    meter.record_turn(
        tenant_id="t1",
        user_id="u1",
        feature_key="chart_qa",
        model="m",
        input_tokens=10,
        output_tokens=10,
        now=now,
    )
    meter.record_turn(
        tenant_id="t1",
        user_id="u1",
        feature_key="chart_qa",
        model="m",
        input_tokens=10,
        output_tokens=10,
        now=now,
    )
    with patch.dict(os.environ, {"LLM_QUOTA_ENFORCEMENT": "true"}):
        get_settings.cache_clear()
        try:
            result = meter.check_quota(
                tenant_id="t1", user_id="u1", feature_key="chart_qa", now=now
            )
        finally:
            get_settings.cache_clear()

    assert result.status == QuotaStatus.HARD_BLOCK
    assert result.limit == 2
    assert result.used == 2
