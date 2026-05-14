# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Tests for the LLM usage meter (THERAPY-f6eg, Phase 3b of THERAPY-bhv).

Covers the recording path, the monthly aggregation window, the OSS
default ``check_quota`` short-circuit, the silent-swallow behavior on
repository failure, and the ``period_yyyymm`` helper.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest
from app.models import QuotaStatus, UsageSummary
from app.repositories import InMemoryLlmUsageRepository
from app.services import LlmUsageMeter, period_yyyymm

if TYPE_CHECKING:
    from datetime import datetime as _dt  # noqa: F401


USER_ID = "user-meter-1"
FEATURE = "chart_qa"
MODEL = "gemini-2.5-flash-lite"


class _Settings:
    """Minimal stand-in for ``app.settings.Settings`` — only the field
    the meter reads matters here."""

    def __init__(self, *, llm_quota_enforcement: str = "off") -> None:
        self.llm_quota_enforcement = llm_quota_enforcement


@pytest.fixture
def repo() -> InMemoryLlmUsageRepository:
    return InMemoryLlmUsageRepository()


@pytest.fixture
def meter(repo: InMemoryLlmUsageRepository) -> LlmUsageMeter:
    return LlmUsageMeter(repo=repo, settings=_Settings())


def test_period_yyyymm_formats_six_chars() -> None:
    assert period_yyyymm(datetime(2026, 5, 13, 12, 0, tzinfo=UTC)) == "202605"
    assert period_yyyymm(datetime(2026, 1, 1, tzinfo=UTC)) == "202601"
    assert period_yyyymm(datetime(2099, 12, 31, tzinfo=UTC)) == "209912"


def test_record_turn_inserts_then_increments(
    meter: LlmUsageMeter, repo: InMemoryLlmUsageRepository
) -> None:
    when1 = datetime(2026, 5, 1, 10, 0, tzinfo=UTC)
    when2 = datetime(2026, 5, 20, 14, 30, tzinfo=UTC)

    meter.record_turn(
        user_id=USER_ID,
        feature_key=FEATURE,
        model=MODEL,
        input_tokens=100,
        output_tokens=50,
        when=when1,
    )
    meter.record_turn(
        user_id=USER_ID,
        feature_key=FEATURE,
        model=MODEL,
        input_tokens=200,
        output_tokens=75,
        when=when2,
    )

    records = repo.list_records(period_yyyymm="202605")
    assert len(records) == 1
    row = records[0]
    assert row.input_tokens == 300
    assert row.output_tokens == 125
    assert row.turn_count == 2
    assert row.first_recorded_at == when1
    assert row.last_recorded_at == when2


def test_record_turn_splits_buckets_per_month(
    meter: LlmUsageMeter, repo: InMemoryLlmUsageRepository
) -> None:
    may = datetime(2026, 5, 31, 23, 0, tzinfo=UTC)
    june = datetime(2026, 6, 1, 1, 0, tzinfo=UTC)

    for when in (may, june):
        meter.record_turn(
            user_id=USER_ID,
            feature_key=FEATURE,
            model=MODEL,
            input_tokens=10,
            output_tokens=5,
            when=when,
        )

    assert len(repo.list_records(period_yyyymm="202605")) == 1
    assert len(repo.list_records(period_yyyymm="202606")) == 1


def test_record_turn_splits_buckets_per_model(
    meter: LlmUsageMeter, repo: InMemoryLlmUsageRepository
) -> None:
    when = datetime(2026, 5, 13, tzinfo=UTC)
    meter.record_turn(
        user_id=USER_ID,
        feature_key=FEATURE,
        model="gemini-2.5-flash-lite",
        input_tokens=10,
        output_tokens=5,
        when=when,
    )
    meter.record_turn(
        user_id=USER_ID,
        feature_key=FEATURE,
        model="gemini-2.5-pro",
        input_tokens=20,
        output_tokens=10,
        when=when,
    )

    records = sorted(repo.list_records(period_yyyymm="202605"), key=lambda r: r.model)
    assert len(records) == 2
    assert records[0].model == "gemini-2.5-flash-lite"
    assert records[1].model == "gemini-2.5-pro"


def test_record_turn_clamps_negative_token_counts(
    meter: LlmUsageMeter, repo: InMemoryLlmUsageRepository
) -> None:
    """A gateway that returns ``None`` collapses to 0 upstream, but
    defensive clamping here guards against a faulty source."""
    when = datetime(2026, 5, 13, tzinfo=UTC)
    meter.record_turn(
        user_id=USER_ID,
        feature_key=FEATURE,
        model=MODEL,
        input_tokens=-5,
        output_tokens=-10,
        when=when,
    )
    record = repo.list_records(period_yyyymm="202605")[0]
    assert record.input_tokens == 0
    assert record.output_tokens == 0
    assert record.turn_count == 1


def test_get_period_usage_sums_inclusive_range(meter: LlmUsageMeter) -> None:
    for when, in_tok in (
        (datetime(2026, 4, 30, tzinfo=UTC), 100),
        (datetime(2026, 5, 13, tzinfo=UTC), 200),
        (datetime(2026, 6, 1, tzinfo=UTC), 300),
        (datetime(2026, 7, 1, tzinfo=UTC), 400),
    ):
        meter.record_turn(
            user_id=USER_ID,
            feature_key=FEATURE,
            model=MODEL,
            input_tokens=in_tok,
            output_tokens=0,
            when=when,
        )

    summary = meter.get_period_usage(
        period_start=datetime(2026, 5, 1, tzinfo=UTC),
        period_end=datetime(2026, 6, 30, tzinfo=UTC),
    )
    assert summary == UsageSummary(input_tokens=500, output_tokens=0, turn_count=2)


def test_get_period_usage_filters_by_user_and_feature(meter: LlmUsageMeter) -> None:
    when = datetime(2026, 5, 13, tzinfo=UTC)
    meter.record_turn(
        user_id="alice",
        feature_key="chart_qa",
        model=MODEL,
        input_tokens=10,
        output_tokens=5,
        when=when,
    )
    meter.record_turn(
        user_id="bob",
        feature_key="chart_qa",
        model=MODEL,
        input_tokens=20,
        output_tokens=10,
        when=when,
    )
    meter.record_turn(
        user_id="alice",
        feature_key="rx_justification",
        model=MODEL,
        input_tokens=40,
        output_tokens=20,
        when=when,
    )

    only_alice_chart = meter.get_period_usage(
        period_start=datetime(2026, 5, 1, tzinfo=UTC),
        period_end=datetime(2026, 5, 31, tzinfo=UTC),
        user_id="alice",
        feature_key="chart_qa",
    )
    assert only_alice_chart == UsageSummary(input_tokens=10, output_tokens=5, turn_count=1)


def test_check_quota_off_by_default(meter: LlmUsageMeter) -> None:
    assert meter.check_quota(user_id=USER_ID, feature_key=FEATURE) == QuotaStatus.OK


def test_check_quota_on_without_config_is_unlimited(
    repo: InMemoryLlmUsageRepository,
) -> None:
    meter = LlmUsageMeter(repo=repo, settings=_Settings(llm_quota_enforcement="on"))
    # OSS has no tenant-config storage; per design doc §11.6 resolution
    # rule 2, missing limits resolve to OK even with enforcement on.
    assert meter.check_quota(user_id=USER_ID, feature_key=FEATURE) == QuotaStatus.OK


def test_record_turn_swallows_repository_failure(caplog) -> None:
    """A metering write must never raise into the chat turn flow."""

    class FailingRepo(InMemoryLlmUsageRepository):
        def record_turn(self, **_kwargs: object) -> None:  # type: ignore[override]
            raise RuntimeError("transient db error")

    meter = LlmUsageMeter(repo=FailingRepo(), settings=_Settings())
    with caplog.at_level(logging.ERROR):
        meter.record_turn(
            user_id=USER_ID,
            feature_key=FEATURE,
            model=MODEL,
            input_tokens=10,
            output_tokens=5,
        )
    assert any("failed to record turn" in r.message for r in caplog.records)
