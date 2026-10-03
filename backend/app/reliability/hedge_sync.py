# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Carry out a :class:`~.hedge.HedgeRun` with real calls on worker threads.

The run decides everything; this only starts legs on an executor, waits
for the first of a leg finishing or the run's timer, and reports back.

Abandoning a leg cannot stop it. The providers' clients are synchronous
and offer no way to cancel a call in flight, so an abandoned leg holds
its worker until its own timeout ends it, and is billed regardless. That
is why legs run on an executor of their own rather than the request
threadpool: an abandoned leg must never take a thread a request needs.
"""

from __future__ import annotations

import contextvars
import time
from concurrent.futures import FIRST_COMPLETED, Executor, Future, wait
from typing import TYPE_CHECKING

from .hedge import (
    Abandon,
    Action,
    Fail,
    FailureKind,
    HedgeRun,
    Leg,
    LegDone,
    LegFailed,
    Return,
    SetTimer,
    StartLeg,
    TimerFired,
)

if TYPE_CHECKING:
    from collections.abc import Callable


def run_hedged_sync[T](
    run: HedgeRun[T],
    call: Callable[[Leg], T],
    *,
    classify: Callable[[BaseException], FailureKind],
    executor: Executor,
    clock: Callable[[], float] = time.monotonic,
) -> T:
    """Drive ``run`` to its end and return the winning answer.

    Raises the error the run failed with. Each leg runs in a copy of the
    caller's context, so tracing and tenant context reach it.
    """
    running: dict[int, Future[T]] = {}
    timer_at = 0.0

    def apply(actions: list[Action[T]]) -> Return[T] | None:
        nonlocal timer_at
        for action in actions:
            match action:
                case StartLeg(index=index, leg=leg):
                    running[index] = executor.submit(contextvars.copy_context().run, call, leg)
                case Abandon(index=index):
                    running.pop(index).cancel()
                case SetTimer(at=at):
                    timer_at = at
                case Return():
                    return action
                case Fail(error=error):
                    raise error
        return None

    outcome = apply(run.begin(clock()))
    while outcome is None:
        index_of = {future: index for index, future in running.items()}
        done, _ = wait(index_of, timeout=max(0.0, timer_at - clock()), return_when=FIRST_COMPLETED)
        now = clock()
        if not done:
            outcome = apply(run.on(TimerFired(), now))
            continue
        for future in done:
            index = index_of[future]
            if index not in running:
                continue  # abandoned by an event handled just before this one
            del running[index]
            error = future.exception()
            event: LegDone[T] | LegFailed = (
                LegDone(index, future.result())
                if error is None
                else LegFailed(index, error, classify(error))
            )
            outcome = apply(run.on(event, now))
            if outcome is not None:
                break
    return outcome.value


__all__ = ["run_hedged_sync"]
