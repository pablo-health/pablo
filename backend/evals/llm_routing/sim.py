# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Drive the production routing policy on a virtual clock.

This is the simulator's driver, the twin of ``app.reliability.hedge_sync``:
both hand events to the same :class:`HedgeRun` and carry out what it
returns. Here a started leg is a fake provider call whose finish time is
known up front, and time jumps from one event to the next, so thousands
of requests through hours of bursty weather take well under a second.

Each request is checked against the invariants every run must keep: no
more legs in flight than the policy allows, an end within the budget,
and nothing returned that ``validate`` did not pass. A violation raises
:class:`InvariantError`.
"""

from __future__ import annotations

import heapq
import random
import statistics
from dataclasses import dataclass
from typing import TYPE_CHECKING

from app.reliability.hedge import (
    Abandon,
    Action,
    Fail,
    FailureKind,
    HedgeRun,
    LegDone,
    LegFailed,
    Return,
    SetTimer,
    StartLeg,
    TimerFired,
)

from .providers import Call, Episodes, Fleet, Outcome, RequestDraws
from .scenarios import PRIMARY, SECONDARY

if TYPE_CHECKING:
    from app.reliability.hedge import HedgePolicy

    from .providers import Calls, Script
    from .scenarios import Scenario


class InvariantError(AssertionError):
    pass


#: Float slack when comparing a request's virtual wall time to the budget.
_EPSILON = 1e-9


@dataclass(frozen=True)
class Answer:
    """What a leg hands back, graded by the oracle."""

    outcome: Outcome


def validate(answer: Answer) -> bool:
    """The caller's check: a truncated answer fails it, a wrong one cannot."""
    return answer.outcome is not Outcome.TRUNCATED


#: How the gateway's classifier sorts each provider error.
_FAILURES = {
    Outcome.ERROR_504: FailureKind.TRANSIENT,
    Outcome.ERROR_429: FailureKind.TRANSIENT,
    Outcome.INVALID: FailureKind.INVALID,
}


@dataclass(frozen=True)
class RequestRecord:
    wall: float
    result: str
    """``ok``, ``wrong`` (passed validation, graded wrong) or ``failed``."""
    billed_calls: int
    """Calls the provider charges for, abandoned ones included."""
    cost: float
    hedged: bool
    """A leg started while another was still running."""


def simulate_request(
    policy: HedgePolicy, fleet: Calls, t0: float, rng: random.Random
) -> RequestRecord:
    run: HedgeRun[Answer] = HedgeRun(policy, validate)
    draws = RequestDraws()
    pending: list[tuple[float, int, Call]] = []
    active: set[int] = set()
    validated: set[int] = set()
    timer_at = t0
    billed = 0
    cost = 0.0
    hedged = False
    now = t0

    def apply(actions: list[Action[Answer]]) -> str | None:
        nonlocal timer_at, billed, cost, hedged
        for action in actions:
            match action:
                case StartLeg(index=index, leg=leg):
                    hedged = hedged or bool(active)
                    call = fleet.call(leg.model, now, draws, rng)
                    billed += call.cost > 0
                    cost += call.cost
                    active.add(index)
                    if len(active) > policy.max_in_flight:
                        raise InvariantError(f"{len(active)} legs in flight")
                    heapq.heappush(pending, (now + call.duration, index, call))
                case Abandon(index=index):
                    active.discard(index)
                case SetTimer(at=at):
                    timer_at = at
                case Return(index=index, value=answer):
                    if index not in validated:
                        raise InvariantError("returned an answer validate() never passed")
                    return "wrong" if answer.outcome is Outcome.WRONG else "ok"
                case Fail():
                    return "failed"
        return None

    result = apply(run.begin(now))
    while result is None:
        while pending and pending[0][1] not in active:
            heapq.heappop(pending)  # abandoned: it finishes, but nobody is listening
        if pending and pending[0][0] <= timer_at:
            now, index, call = heapq.heappop(pending)
            active.discard(index)
            answer = Answer(call.outcome)
            if validate(answer):
                validated.add(index)
            kind = _FAILURES.get(call.outcome)
            event: LegDone[Answer] | LegFailed = (
                LegDone(index, answer)
                if kind is None
                else LegFailed(index, RuntimeError(call.outcome.value), kind)
            )
            result = apply(run.on(event, now))
        else:
            now = timer_at
            result = apply(run.on(TimerFired(), now))

    wall = now - t0
    if wall > policy.budget + _EPSILON:
        raise InvariantError(f"request took {wall:.1f}s")
    return RequestRecord(wall, result, billed, cost, hedged)


@dataclass(frozen=True)
class Report:
    n: int
    p50: float
    p95: float
    p99: float
    max: float
    over_budget: float
    """Ended after the budget. The invariant makes this zero; it is
    reported so the claim stays visible."""
    at_budget: float
    """Ran the whole budget: someone waited the full bound for an answer
    or for a failure."""
    hedge_rate: float
    double_billed: float
    cost_per_request: float
    wrong_rate: float
    """Of answered requests, the share answered wrongly."""
    failure_rate: float


def _quantile(sorted_walls: list[float], q: float) -> float:
    return sorted_walls[min(len(sorted_walls) - 1, int(q * len(sorted_walls)))]


def summarize(records: list[RequestRecord], budget: float) -> Report:
    walls = sorted(r.wall for r in records)
    n = len(records)
    answered = [r for r in records if r.result != "failed"]
    return Report(
        n=n,
        p50=statistics.median(walls),
        p95=_quantile(walls, 0.95),
        p99=_quantile(walls, 0.99),
        max=walls[-1],
        over_budget=sum(r.wall > budget + _EPSILON for r in records) / n,
        at_budget=sum(r.wall >= budget - _EPSILON for r in records) / n,
        hedge_rate=sum(r.hedged for r in records) / n,
        double_billed=sum(r.billed_calls > 1 for r in records) / n,
        cost_per_request=sum(r.cost for r in records) / n,
        wrong_rate=sum(r.result == "wrong" for r in answered) / max(1, len(answered)),
        failure_rate=(n - len(answered)) / n,
    )


def simulate(scenario: Scenario, policy: HedgePolicy, *, n: int, seed: int) -> Report:
    """Run ``n`` requests of ``scenario`` through ``policy``.

    The weather is drawn from ``seed`` alone, so two policies run with
    the same seed face the same storms.
    """
    weather_rng = random.Random(seed)
    horizon = n * scenario.spacing + 2 * policy.budget
    weather = {
        name: Episodes.generate(weather_rng, horizon, scenario.calm_mean, scenario.storm_mean)
        for name in (PRIMARY, SECONDARY)
    }
    fleet = Fleet({PRIMARY: scenario.primary, SECONDARY: scenario.secondary}, weather, scenario.rho)
    rng = random.Random(seed + 1)
    records = [simulate_request(policy, fleet, i * scenario.spacing, rng) for i in range(n)]
    return summarize(records, policy.budget)


def simulate_script(policy: HedgePolicy, script: Script) -> RequestRecord:
    """Run one request whose every call is written out in ``script``.

    The same driver and invariants as :func:`simulate`, with nothing left
    to chance: the place to pin a single story down, such as a primary
    that fails at once and a fallback that answers.
    """
    return simulate_request(policy, script, 0.0, random.Random(0))
