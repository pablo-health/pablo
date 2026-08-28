# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.
"""Unit tests for the repeat-sampling aggregation used by the note-gen eval runner.

No network, no model calls, no Braintrust — this only exercises the pure
aggregation logic in ``backend.evals.sampling``.
"""

from __future__ import annotations

import pytest

from backend.evals.sampling import SampleResult, aggregate_sample_verdicts


def _sample(hard_failures: list[str] | None = None) -> SampleResult:
    failures = hard_failures or []
    return SampleResult(
        hard_failures=failures,
        advisory_omissions=[],
        judge_passes=not failures,
        gen_seconds=1.0,
        generated_chars=100,
        hallucinated_facts=[],
        missing_facts=[],
        judge_notes="ok",
    )


def test_all_samples_pass_case_passes() -> None:
    samples = [_sample(), _sample(), _sample()]
    result = aggregate_sample_verdicts(samples)
    assert result.passed is True
    assert result.n_samples == 3
    assert result.n_failed_samples == 0
    assert result.hard_failures == []


def test_one_failing_sample_fails_the_case() -> None:
    samples = [
        _sample(),
        _sample(hard_failures=["ASSESSMENT hallucination: invented diagnosis"]),
        _sample(),
    ]
    result = aggregate_sample_verdicts(samples)
    assert result.passed is False
    assert result.n_samples == 3
    assert result.n_failed_samples == 1
    assert result.hard_failures == ["ASSESSMENT hallucination: invented diagnosis"]


def test_n_equals_one_parity_with_single_sample() -> None:
    sample = _sample(hard_failures=["hallucination (subjective): invented quote"])
    result = aggregate_sample_verdicts([sample])
    assert result.passed is False
    assert result.n_samples == 1
    assert result.n_failed_samples == 1
    assert result.hard_failures == sample.hard_failures
    assert result.samples == [sample]


def test_empty_sample_list_raises() -> None:
    with pytest.raises(ValueError, match="at least one sample"):
        aggregate_sample_verdicts([])


def test_hard_failure_union_is_deduped_and_order_preserving() -> None:
    samples = [
        _sample(hard_failures=["hallucination A", "hallucination B"]),
        _sample(hard_failures=["hallucination B", "hallucination C"]),
        _sample(hard_failures=["hallucination A"]),
    ]
    result = aggregate_sample_verdicts(samples)
    assert result.hard_failures == ["hallucination A", "hallucination B", "hallucination C"]
    assert result.n_failed_samples == 3
