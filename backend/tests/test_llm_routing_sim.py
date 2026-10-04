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

from typing import TYPE_CHECKING

import pytest
from app.reliability.hedge import INTERACTIVE_STALL_AFTER
from evals.llm_routing.scenarios import (
    BUDGET,
    BY_NAME,
    SCENARIOS,
    SECONDARY,
    STORIES,
    Scenario,
    baseline_policy,
    hedged_policy,
    interactive_policy,
)
from evals.llm_routing.sim import Report, simulate, simulate_script

if TYPE_CHECKING:
    from app.reliability.hedge import HedgePolicy
    from evals.llm_routing.sim import RequestRecord

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


#: What a clinician will sit on the hours step for.
INTERACTIVE_BUDGET = 8.0


def _story(name: str, policy: HedgePolicy) -> RequestRecord:
    story = next(s for s in STORIES if s.name == name)
    return simulate_script(policy, story.script())


class TestStories:
    """Single requests, call by call, under the interactive policy."""

    def test_the_slow_parse_that_was_seen_is_halved_and_a_fallback_ends_it(self) -> None:
        """The primary stalled to its 15 s bound and its retry took 6.9 s:
        21.9 s. Its retry now starts at the stall threshold instead."""
        assert _story("stall_then_slow_retry", baseline_policy()).wall == pytest.approx(21.9)
        assert _story("stall_then_slow_retry", interactive_policy()).wall == pytest.approx(10.9)
        with_fallback = _story("stall_then_slow_retry", interactive_policy((SECONDARY,)))
        assert with_fallback.wall < INTERACTIVE_BUDGET

    @pytest.mark.parametrize("fallbacks", [(), (SECONDARY,)], ids=["retry", "fallback"])
    def test_a_primary_that_fails_at_once_is_answered_at_once(
        self, fallbacks: tuple[str, ...]
    ) -> None:
        record = _story("errors_at_once", interactive_policy(fallbacks))
        assert record.result == "ok"
        assert record.wall == pytest.approx(0.3 + 1.6)

    def test_a_primary_that_fails_twice_is_answered_by_the_fallback(self) -> None:
        record = _story("errors_twice", interactive_policy((SECONDARY,)))
        assert record.result == "ok"
        assert record.wall == pytest.approx(0.3 + 1.6)

    def test_a_primary_that_fails_twice_with_no_fallback_fails_at_once(self) -> None:
        record = _story("errors_twice", interactive_policy())
        assert record.result == "failed"
        assert record.wall == pytest.approx(0.6)

    @pytest.mark.parametrize("fallbacks", [(), (SECONDARY,)], ids=["retry", "fallback"])
    def test_a_stalled_primary_costs_only_the_threshold(self, fallbacks: tuple[str, ...]) -> None:
        record = _story("stalls", interactive_policy(fallbacks))
        assert record.result == "ok"
        assert record.wall == pytest.approx(INTERACTIVE_STALL_AFTER + 1.6)
        assert record.wall < INTERACTIVE_BUDGET

    def test_a_primary_failing_late_was_already_hedged(self) -> None:
        before = _story("errors_after_10s", baseline_policy())
        after = _story("errors_after_10s", interactive_policy())
        assert before.wall == pytest.approx(11.6)
        assert after.wall == pytest.approx(INTERACTIVE_STALL_AFTER + 1.6)

    def test_when_everything_fails_the_error_is_quick(self) -> None:
        record = _story("everything_fails", interactive_policy((SECONDARY,)))
        assert record.result == "failed"
        assert record.wall < 1.0

    @pytest.mark.parametrize("fallbacks", [(), (SECONDARY,)], ids=["retry", "fallback"])
    def test_when_everything_stalls_the_deadline_holds(self, fallbacks: tuple[str, ...]) -> None:
        record = _story("everything_stalls", interactive_policy(fallbacks))
        assert record.result == "failed"
        assert record.wall <= BUDGET


def test_the_interactive_policy_keeps_the_invariants() -> None:
    for scenario in SCENARIOS:
        for policy in (interactive_policy(), interactive_policy((SECONDARY,))):
            report = simulate(scenario, policy, n=N, seed=SEED)
            assert report.over_budget == 0.0


def test_the_interactive_policy_absorbs_single_stalls() -> None:
    today = simulate(BY_NAME["single_stalls"], baseline_policy(), n=N, seed=SEED)
    now = simulate(BY_NAME["single_stalls"], interactive_policy(), n=N, seed=SEED)
    assert today.p99 >= 15.0
    assert now.p99 <= INTERACTIVE_BUDGET
    assert now.failure_rate <= today.failure_rate + 0.001


def test_hedging_never_makes_a_wrong_answer_more_likely() -> None:
    """Validation cannot catch a plausible wrong answer, and racing two
    providers must not select for one."""
    for name in ("healthy", "bursty"):
        today, hedged = _run(name, hedged=False), _run(name)
        assert abs(hedged.wrong_rate - today.wrong_rate) <= 0.01
