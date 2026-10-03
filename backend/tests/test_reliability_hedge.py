# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""The routing policy, driven one event at a time.

No threads and no clock: each test hands :class:`HedgeRun` an event and
the time it happened, and reads back what the policy wants done.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest
from app.reliability.hedge import (
    Abandon,
    Fail,
    FailureKind,
    HedgePolicy,
    HedgeRun,
    InvalidResultError,
    Leg,
    LegDone,
    LegFailed,
    LegTimeoutError,
    Return,
    SetTimer,
    StartLeg,
    TimerFired,
)

if TYPE_CHECKING:
    from collections.abc import Callable

A = Leg("model-a", 15.0)
B = Leg("model-b", 15.0)


def _policy(
    *,
    legs: tuple[Leg, ...] = (A, B, A),
    budget: float = 25.0,
    hedge_after: float | None = None,
    max_in_flight: int = 2,
    retry_invalid: bool = False,
) -> HedgePolicy:
    return HedgePolicy(legs, budget, hedge_after, max_in_flight, retry_invalid)


def _run(policy: HedgePolicy, validate: Callable[[str], bool] = lambda _v: True) -> HedgeRun[str]:
    return HedgeRun(policy, validate)


class TestStart:
    def test_begin_starts_the_first_leg_and_waits_for_its_timeout(self) -> None:
        run = _run(_policy())
        assert run.begin(0.0) == [StartLeg(0, A), SetTimer(15.0)]
        assert run.in_flight == 1

    def test_begin_twice_is_an_error(self) -> None:
        run = _run(_policy())
        run.begin(0.0)
        with pytest.raises(RuntimeError):
            run.begin(1.0)

    def test_with_hedging_the_first_timer_is_the_hedge(self) -> None:
        run = _run(_policy(hedge_after=6.0))
        assert run.begin(0.0) == [StartLeg(0, A), SetTimer(6.0)]


class TestSequential:
    def test_an_answer_is_returned(self) -> None:
        run = _run(_policy())
        run.begin(0.0)
        assert run.on(LegDone(0, "rules"), 1.6) == [Return(0, "rules")]
        assert run.finished

    def test_a_timeout_abandons_the_leg_and_starts_the_next(self) -> None:
        run = _run(_policy())
        run.begin(0.0)
        # The next leg gets the 10 s left of the 25 s budget, not its own 15.
        late_b = Leg("model-b", 10.0)
        assert run.on(TimerFired(), 15.0) == [Abandon(0), StartLeg(1, late_b), SetTimer(25.0)]

    def test_a_timer_that_fires_early_changes_nothing(self) -> None:
        run = _run(_policy())
        run.begin(0.0)
        assert run.on(TimerFired(), 14.9) == [SetTimer(15.0)]

    def test_a_fast_transient_error_starts_the_next_leg_at_once(self) -> None:
        run = _run(_policy())
        run.begin(0.0)
        actions = run.on(LegFailed(0, RuntimeError("504"), FailureKind.TRANSIENT), 0.3)
        assert actions == [StartLeg(1, B), SetTimer(15.3)]

    def test_a_late_answer_from_an_abandoned_leg_is_ignored(self) -> None:
        run = _run(_policy())
        run.begin(0.0)
        run.on(TimerFired(), 15.0)
        assert run.on(LegDone(0, "late"), 16.0) == []
        assert run.in_flight == 1
        assert run.on(LegDone(1, "rules"), 17.0) == [Return(1, "rules")]

    def test_a_fatal_error_ends_the_run_without_trying_another_leg(self) -> None:
        run = _run(_policy())
        run.begin(0.0)
        error = PermissionError("403")
        assert run.on(LegFailed(0, error, FailureKind.FATAL), 0.2) == [Fail(error)]

    def test_running_out_of_legs_fails_with_the_last_error(self) -> None:
        run = _run(_policy(legs=(A, A)))
        run.begin(0.0)
        run.on(LegFailed(0, RuntimeError("first"), FailureKind.TRANSIENT), 1.0)
        last = RuntimeError("second")
        assert run.on(LegFailed(1, last, FailureKind.TRANSIENT), 2.0) == [Fail(last)]

    def test_running_out_after_a_timeout_fails_with_a_timeout(self) -> None:
        run = _run(_policy(legs=(A,)))
        run.begin(0.0)
        actions = run.on(TimerFired(), 15.0)
        assert actions[0] == Abandon(0)
        assert isinstance(actions[1], Fail)
        assert isinstance(actions[1].error, LegTimeoutError)
        assert actions[1].error.leg == A

    def test_events_after_the_end_are_ignored(self) -> None:
        run = _run(_policy())
        run.begin(0.0)
        run.on(LegDone(0, "rules"), 1.0)
        assert run.on(TimerFired(), 2.0) == []


class TestBudget:
    def test_no_leg_starts_once_the_budget_is_spent(self) -> None:
        run = _run(_policy(budget=10.0))
        run.begin(0.0)
        error = RuntimeError("504")
        assert run.on(LegFailed(0, error, FailureKind.TRANSIENT), 10.0) == [Fail(error)]

    def test_a_late_leg_ends_with_the_budget(self) -> None:
        run = _run(_policy())
        run.begin(0.0)
        run.on(TimerFired(), 15.0)  # leg 1 starts at 15 with 10 s left
        actions = run.on(TimerFired(), 25.0)
        assert actions[0] == Abandon(1)
        assert isinstance(actions[-1], Fail)
        assert isinstance(actions[-1].error, LegTimeoutError)
        assert actions[-1].error.leg == Leg("model-b", 10.0)

    def test_a_fast_failure_leaves_the_next_leg_its_full_timeout(self) -> None:
        run = _run(_policy())
        run.begin(0.0)
        actions = run.on(LegFailed(0, RuntimeError("429"), FailureKind.TRANSIENT), 0.1)
        assert actions[0] == StartLeg(1, B)

    def test_no_hedge_is_scheduled_past_the_budget(self) -> None:
        run = _run(_policy(hedge_after=6.0, budget=5.0))
        assert run.begin(0.0) == [StartLeg(0, Leg("model-a", 5.0)), SetTimer(5.0)]


class TestHedge:
    def test_a_slow_leg_gets_company_after_hedge_after(self) -> None:
        run = _run(_policy(hedge_after=6.0))
        run.begin(0.0)
        # Both slots are taken, so the next wake is the first leg's timeout.
        assert run.on(TimerFired(), 6.0) == [StartLeg(1, B), SetTimer(15.0)]
        assert run.in_flight == 2

    def test_the_first_answer_wins_and_the_other_leg_is_abandoned(self) -> None:
        run = _run(_policy(hedge_after=6.0))
        run.begin(0.0)
        run.on(TimerFired(), 6.0)
        assert run.on(LegDone(1, "from b"), 7.5) == [Abandon(0), Return(1, "from b")]

    def test_never_more_than_max_in_flight(self) -> None:
        run = _run(_policy(hedge_after=6.0, max_in_flight=2))
        run.begin(0.0)
        run.on(TimerFired(), 6.0)
        assert run.on(TimerFired(), 12.0) == [SetTimer(15.0)]
        assert run.in_flight == 2

    def test_a_timeout_while_hedged_frees_a_slot_for_the_next_leg(self) -> None:
        run = _run(_policy(hedge_after=6.0))
        run.begin(0.0)
        run.on(TimerFired(), 6.0)
        late_a = Leg("model-a", 10.0)
        assert run.on(TimerFired(), 15.0) == [Abandon(0), StartLeg(2, late_a), SetTimer(21.0)]

    def test_without_hedging_a_slow_leg_is_left_alone(self) -> None:
        run = _run(_policy())
        run.begin(0.0)
        assert run.on(TimerFired(), 6.0) == [SetTimer(15.0)]
        assert run.in_flight == 1


class TestValidation:
    def test_an_invalid_answer_is_never_returned(self) -> None:
        run = _run(_policy(retry_invalid=True), validate=lambda v: v != "garbled")
        run.begin(0.0)
        assert run.on(LegDone(0, "garbled"), 1.0) == [StartLeg(1, B), SetTimer(16.0)]
        assert run.on(LegDone(1, "rules"), 2.0) == [Return(1, "rules")]

    def test_an_invalid_answer_ignored_while_another_leg_runs(self) -> None:
        run = _run(_policy(hedge_after=6.0, retry_invalid=True), validate=lambda v: v != "garbled")
        run.begin(0.0)
        run.on(TimerFired(), 6.0)
        assert run.on(LegDone(1, "garbled"), 7.0) == [SetTimer(12.0)]
        assert run.on(LegDone(0, "rules"), 8.0) == [Return(0, "rules")]

    def test_without_retry_invalid_an_invalid_answer_ends_the_run(self) -> None:
        run = _run(_policy(), validate=lambda v: v != "garbled")
        run.begin(0.0)
        actions = run.on(LegDone(0, "garbled"), 1.0)
        assert len(actions) == 1
        assert isinstance(actions[0], Fail)
        assert isinstance(actions[0].error, InvalidResultError)

    def test_an_invalid_failure_follows_retry_invalid(self) -> None:
        error = ValueError("not JSON")
        stays = _run(_policy())
        stays.begin(0.0)
        assert stays.on(LegFailed(0, error, FailureKind.INVALID), 1.0) == [Fail(error)]

        moves_on = _run(_policy(retry_invalid=True))
        moves_on.begin(0.0)
        assert moves_on.on(LegFailed(0, error, FailureKind.INVALID), 1.0)[0] == StartLeg(1, B)


class TestPolicy:
    def test_one_model_is_one_attempt_and_one_retry(self) -> None:
        policy = HedgePolicy.for_models("flash", attempt_timeout=15.0, budget=25.0)
        assert policy.legs == (Leg("flash", 15.0), Leg("flash", 15.0))
        assert policy.hedge_after is None
        assert not policy.retry_invalid

    def test_fallbacks_are_tried_in_order_then_each_again(self) -> None:
        policy = HedgePolicy.for_models(
            "a", ["b", "a", "c"], attempt_timeout=10.0, budget=25.0, hedge_after=6.0
        )
        assert [leg.model for leg in policy.legs] == ["a", "b", "c", "a", "b", "c"]
        assert policy.retry_invalid

    @pytest.mark.parametrize(
        "overrides",
        [{"legs": ()}, {"budget": 0.0}, {"max_in_flight": 0}, {"hedge_after": 0.0}],
    )
    def test_nonsense_is_refused(self, overrides: dict[str, Any]) -> None:
        with pytest.raises(ValueError, match=r"must be|needs at least"):
            _policy(**overrides)

    def test_provider_health_reorders_legs(self) -> None:
        class BPreferred:
            def order(self, legs: tuple[Leg, ...]) -> tuple[Leg, ...]:
                return tuple(sorted(legs, key=lambda leg: leg.model != "model-b"))

        run: HedgeRun[str] = HedgeRun(_policy(), health=BPreferred())
        assert run.begin(0.0)[0] == StartLeg(0, B)
