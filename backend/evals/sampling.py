# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.
"""Aggregate repeated generate+judge samples of a single eval case.

A single generation is a coin flip: the same case can pass on one run and
fail on the next. ``run_note_generation.py`` can now generate and judge a
case ``N`` times and roll the samples up into one case-level verdict via
``aggregate_sample_verdicts``. This module has no dependency on the harness,
the judge, or Braintrust, so it can be unit tested without network access or
a live model.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class SampleResult:
    """The outcome of one generate+judge run of a case."""

    hard_failures: list[str]
    advisory_omissions: list[str]
    judge_passes: bool
    gen_seconds: float
    generated_chars: int
    hallucinated_facts: list[dict[str, str]]
    missing_facts: list[dict[str, str]]
    judge_notes: str

    def to_dict(self) -> dict[str, object]:
        return {
            "hard_failures": self.hard_failures,
            "advisory_omissions": self.advisory_omissions,
            "judge_passes": self.judge_passes,
            "gen_seconds": self.gen_seconds,
            "generated_chars": self.generated_chars,
            "hallucinated_facts": self.hallucinated_facts,
            "missing_facts": self.missing_facts,
            "judge_notes": self.judge_notes,
        }


@dataclass
class CaseAggregate:
    """The case-level verdict rolled up from one or more samples."""

    passed: bool
    n_samples: int
    n_failed_samples: int
    hard_failures: list[str]
    samples: list[SampleResult] = field(default_factory=list)


def aggregate_sample_verdicts(samples: list[SampleResult]) -> CaseAggregate:
    """Roll up repeated samples of one case into a single worst-case verdict.

    The case passes only if every sample has zero hard failures — one bad
    sample out of N fails the case. ``hard_failures`` is the deduped,
    order-preserving union across all samples, so a caller can see every
    distinct failure without wading through per-sample duplicates.
    """
    if not samples:
        raise ValueError("aggregate_sample_verdicts requires at least one sample")

    n_failed_samples = sum(1 for s in samples if s.hard_failures)
    passed = n_failed_samples == 0

    union: list[str] = []
    seen: set[str] = set()
    for s in samples:
        for failure in s.hard_failures:
            if failure not in seen:
                seen.add(failure)
                union.append(failure)

    return CaseAggregate(
        passed=passed,
        n_samples=len(samples),
        n_failed_samples=n_failed_samples,
        hard_failures=union,
        samples=list(samples),
    )
