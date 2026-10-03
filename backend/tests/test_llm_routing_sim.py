# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""The routing policy, proven against simulated providers.

Every scenario runs the production :class:`HedgeRun` on a virtual clock
(``evals/llm_routing``), seeded, so the bounds below are exact for a given
seed rather than flaky. Each request is also checked against the policy's
invariants as it runs: never more legs in flight than allowed, an end
within the budget, and no answer returned that validation did not pass.
A violation raises inside ``simulate``.

Today's policy (one model, one retry) runs beside the hedged one, so a
bound can say "no worse than today" rather than quote a number.
"""

from __future__ import annotations

import pytest
from evals.llm_routing.scenarios import (
    BY_NAME,
    SCENARIOS,
    Scenario,
    baseline_policy,
    hedged_policy,
)
from evals.llm_routing.sim import Report, simulate

N = 3000
SEED = 7


def _run(name: str, *, hedged: bool = True) -> Report:
    policy = hedged_policy() if hedged else baseline_policy()
    return simulate(BY_NAME[name], policy, n=N, seed=SEED)


@pytest.mark.parametrize("scenario", SCENARIOS, ids=lambda s: s.name)
def test_invariants_hold_under_both_policies(scenario: Scenario) -> None:
    for policy in (baseline_policy(), hedged_policy()):
        report = simulate(scenario, policy, n=N, seed=SEED)
        assert report.over_budget == 0.0
        assert report.max <= policy.budget


def test_healthy_providers_are_rarely_hedged() -> None:
    report = _run("healthy")
    assert report.hedge_rate <= 0.06
    assert report.p99 <= 9.0
    assert report.failure_rate <= 0.001


def test_single_stalls_are_absorbed() -> None:
    today, hedged = _run("single_stalls", hedged=False), _run("single_stalls")
    assert today.p99 >= 15.0, "the scenario should hurt without hedging"
    assert hedged.p99 <= 10.0
    assert hedged.at_budget == 0.0


def test_bursty_degradation_rarely_runs_to_the_deadline() -> None:
    today, hedged = _run("bursty", hedged=False), _run("bursty")
    assert today.at_budget > 0.01, "the scenario should run requests into the deadline"
    assert hedged.at_budget <= 0.001
    assert hedged.p99 <= 12.0
    assert hedged.failure_rate <= 0.001


def test_a_primary_outage_costs_only_the_fast_failure() -> None:
    """Fast 504s hand over at once: the request sees the secondary's own
    latency, plus the 0.3 s the primary took to say no."""
    secondary_alone = simulate(BY_NAME["healthy"], baseline_policy(), n=N, seed=SEED)
    outage = _run("primary_outage")
    assert outage.p50 <= secondary_alone.p50 + 0.5
    assert outage.p95 <= secondary_alone.p95 + 1.0
    assert outage.failure_rate <= 0.001


def test_a_slow_secondary_does_no_harm() -> None:
    today, hedged = _run("slow_secondary", hedged=False), _run("slow_secondary")
    assert hedged.p50 <= today.p50 + 0.1
    assert hedged.p95 <= today.p95 + 0.5
    assert hedged.p99 <= today.p99
    assert hedged.failure_rate <= today.failure_rate


def test_independent_degradation_is_hedged_away() -> None:
    today, hedged = _run("both_degraded_rho0", hedged=False), _run("both_degraded_rho0")
    assert hedged.p95 <= today.p95 / 2
    assert hedged.at_budget <= 0.005


def test_a_flapping_primary_is_ridden_out() -> None:
    today, hedged = _run("flapping", hedged=False), _run("flapping")
    assert today.failure_rate > 0.2, "the scenario should hurt without a fallback"
    assert hedged.failure_rate <= 0.005
    assert hedged.p95 <= 8.0


def test_hedging_never_makes_a_wrong_answer_more_likely() -> None:
    """Validation cannot catch a plausible wrong answer, and racing two
    providers must not select for one."""
    for name in ("healthy", "bursty"):
        today, hedged = _run(name, hedged=False), _run(name)
        assert abs(hedged.wrong_rate - today.wrong_rate) <= 0.01
