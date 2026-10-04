# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Route one request across several model calls, as a pure state machine.

A request is served by one or more *legs*: calls to a model, each bounded
by its own timeout. The policy decides when a leg starts, when one is
given up on, which answer wins and when the request has failed. It never
sleeps, spawns or reads a clock: a driver feeds it events with the time
they happened and carries out the actions it returns.

Two drivers share this one policy. :func:`.hedge_sync.run_hedged_sync`
runs legs on threads against real providers; the simulator under
``evals/llm_routing`` runs them on a virtual clock against fake ones. So
whatever the simulator shows about thresholds and ordering is a statement
about the code that serves requests, not about a model of it.

The rules, in full:

- Legs run in order. The first starts at once.
- A leg that fails transiently (rate limit, 5xx, dropped connection) or
  outlives its timeout makes room for the next leg straight away.
- A leg whose model refuses the request outright (no access, no such
  model) makes room too, and that model's later legs are skipped:
  asking it again would get the same answer.
- With ``hedge_after`` set, a leg still running after that long gets
  company: the next leg starts beside it, up to ``max_in_flight``.
- The first answer that passes the caller's ``validate`` wins, and every
  other leg still running is abandoned. An answer that fails it is never
  returned; whether the next leg is tried is ``retry_invalid``.
- A fatal failure (the request itself is wrong) ends the run at once.
- Nothing outlasts ``budget``. A leg starting late is given only what is
  left of it as its timeout, and none starts once it has run out.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence


#: How long an interactive call's leg runs alone before the next one starts
#: beside it. A healthy parse answers in 1-3 s and its slowest real answers
#: take 6-8 s; the simulator (``evals/llm_routing``) puts the p99 of a
#: stalling primary near 8 s at this delay, for a second call on about one
#: request in eight.
INTERACTIVE_STALL_AFTER = 4.0


class FailureKind(Enum):
    """What a failed leg says about the legs that might follow it."""

    TRANSIENT = "transient"
    """Rate limit, 5xx, timeout: another leg may well succeed."""

    INVALID = "invalid"
    """An answer came back but cannot be used: malformed, or rejected by
    ``validate``. Another provider may answer properly."""

    PERMANENT = "permanent"
    """This model will not answer this request however often it is asked
    (no access, no such model, a request it rejects). Its later legs are
    skipped; another model's may still answer."""

    FATAL = "fatal"
    """The request itself is at fault; every leg would fail the same way."""


@dataclass(frozen=True)
class Leg:
    model: str
    timeout: float


@dataclass(frozen=True)
class HedgePolicy:
    legs: tuple[Leg, ...]
    budget: float
    """Seconds from the start to the end of the run, retries included."""

    hedge_after: float | None = None
    """Start the next leg beside a running one after this long. ``None``
    runs legs strictly one after another."""

    max_in_flight: int = 2
    retry_invalid: bool = False
    """Whether an unusable answer moves on to the next leg, as a timeout
    does. Off, an unusable answer ends the run."""

    def __post_init__(self) -> None:
        if not self.legs:
            raise ValueError("a hedge policy needs at least one leg")
        if self.budget <= 0 or self.max_in_flight < 1:
            raise ValueError("budget must be positive and max_in_flight at least 1")
        if self.hedge_after is not None and self.hedge_after <= 0:
            raise ValueError("hedge_after must be positive when set")

    @classmethod
    def for_models(
        cls,
        primary: str,
        fallbacks: Sequence[str] = (),
        *,
        attempt_timeout: float,
        budget: float,
        hedge_after: float | None = None,
        max_in_flight: int = 2,
    ) -> HedgePolicy:
        """Each model once, in order, then each once more.

        With no fallbacks that is one attempt and one retry on a single
        model. An unusable answer moves on only when there is another
        model to move on to; asking the same one again is not a fix.
        """
        models = tuple(dict.fromkeys((primary, *fallbacks)))
        return cls(
            legs=tuple(Leg(model, attempt_timeout) for model in models * 2),
            budget=budget,
            hedge_after=hedge_after,
            max_in_flight=max_in_flight,
            retry_invalid=len(models) > 1,
        )

    @classmethod
    def interactive(
        cls,
        primary: str,
        fallbacks: Sequence[str] = (),
        *,
        attempt_timeout: float,
        budget: float,
        stall_after: float | None,
        max_in_flight: int = 2,
    ) -> HedgePolicy:
        """For a call someone is waiting on: hand over early, retry once.

        The primary, then each fallback once, then the primary once more.
        With no fallbacks that is the primary and one retry on it, so the
        retry is the fallback. A failure moves on at once, and a leg still
        running after ``stall_after`` gets the next one beside it, so a
        stalled or failing primary costs at most ``stall_after`` before
        another leg is answering. The same model is never asked more than
        twice, and never again after it refuses the request outright.
        """
        others = tuple(m for m in dict.fromkeys(fallbacks) if m != primary)
        return cls(
            legs=tuple(Leg(model, attempt_timeout) for model in (primary, *others, primary)),
            budget=budget,
            hedge_after=stall_after,
            max_in_flight=max_in_flight,
            retry_invalid=bool(others),
        )


class ProviderHealth(Protocol):
    """Reorders legs from what is known about providers right now.

    A hook for a later circuit breaker; nothing implements it yet.
    """

    def order(self, legs: tuple[Leg, ...]) -> tuple[Leg, ...]:
        """Return the legs in the order to try them."""


class LegTimeoutError(TimeoutError):
    def __init__(self, leg: Leg) -> None:
        super().__init__(f"{leg.model} gave no answer within {leg.timeout:g}s")
        self.leg = leg


class InvalidResultError(ValueError):
    """A leg's answer was rejected by the caller's ``validate``."""


def _root_name(error: BaseException) -> str:
    """The class of the error at the bottom of ``error``'s cause chain.

    A provider's gateway wraps what went wrong (a read timeout, a 503) in
    errors of its own; the bottom one is the one worth logging. A class
    name carries no request content, so it is safe to log.
    """
    seen: set[int] = set()
    while error.__cause__ is not None and id(error) not in seen:
        seen.add(id(error))
        error = error.__cause__
    return type(error).__name__


# -- events (driver -> policy) ------------------------------------------------


@dataclass(frozen=True)
class LegDone[T]:
    index: int
    value: T


@dataclass(frozen=True)
class LegFailed:
    index: int
    error: BaseException
    kind: FailureKind


@dataclass(frozen=True)
class TimerFired:
    pass


# -- actions (policy -> driver) -----------------------------------------------


@dataclass(frozen=True)
class StartLeg:
    index: int
    leg: Leg


@dataclass(frozen=True)
class Abandon:
    """Stop waiting on this leg. Its call may still finish, and be billed."""

    index: int


@dataclass(frozen=True)
class SetTimer:
    """Send ``TimerFired`` at this time unless another event comes first.
    Replaces any earlier timer."""

    at: float


@dataclass(frozen=True)
class Return[T]:
    index: int
    value: T


@dataclass(frozen=True)
class Fail:
    error: BaseException


type Event[T] = LegDone[T] | LegFailed | TimerFired
type Action[T] = StartLeg | Abandon | SetTimer | Return[T] | Fail


class HedgeRun[T]:
    """One request's progress through a :class:`HedgePolicy`."""

    def __init__(
        self,
        policy: HedgePolicy,
        validate: Callable[[T], bool] = lambda _value: True,
        health: ProviderHealth | None = None,
    ) -> None:
        self._policy = policy
        self._legs = health.order(policy.legs) if health is not None else policy.legs
        self._validate = validate
        self._begun = False
        self._budget_ends = 0.0
        self._started: dict[int, Leg] = {}
        self._deadlines: dict[int, float] = {}
        self._next = 0
        self._last_start = 0.0
        self._last_error: BaseException | None = None
        self._finished = False
        self._refused: set[str] = set()
        self._winner: int | None = None
        self.trail: list[tuple[int, str, str]] = []
        """``(leg index, model, what happened)`` for every leg that did not
        win, in the order the run learned of it: an error's class name,
        ``timeout``, ``invalid`` or ``abandoned``. Model names and error
        classes only, so it is safe to log."""

    @property
    def legs(self) -> tuple[Leg, ...]:
        return self._legs

    @property
    def in_flight(self) -> int:
        return len(self._deadlines)

    @property
    def finished(self) -> bool:
        return self._finished

    @property
    def started(self) -> int:
        """Legs started so far, abandoned ones included."""
        return len(self._started)

    @property
    def winner(self) -> int | None:
        """The index of the leg whose answer was returned, once one was."""
        return self._winner

    def begin(self, now: float) -> list[Action[T]]:
        if self._begun:
            raise RuntimeError("a hedge run can only begin once")
        self._begun = True
        self._budget_ends = now + self._policy.budget
        return self._advance(now)

    def on(self, event: Event[T], now: float) -> list[Action[T]]:
        if self._finished:
            return []
        match event:
            case TimerFired():
                return self._advance(now)
            case LegDone(index=index, value=value) if index in self._deadlines:
                del self._deadlines[index]
                if self._validate(value):
                    self._winner = index
                    return self._finish(Return(index, value))
                rejected = InvalidResultError(f"{self._legs[index].model} gave an unusable answer")
                return self._failed(index, rejected, FailureKind.INVALID, now)
            case LegFailed(index=index, error=error, kind=kind) if index in self._deadlines:
                del self._deadlines[index]
                return self._failed(index, error, kind, now)
        # A leg that was already abandoned: its answer is no longer wanted.
        return []

    def _failed(
        self, index: int, error: BaseException, kind: FailureKind, now: float
    ) -> list[Action[T]]:
        self._last_error = error
        what = "invalid" if isinstance(error, InvalidResultError) else _root_name(error)
        self.trail.append((index, self._legs[index].model, what))
        if kind is FailureKind.PERMANENT:
            self._refused.add(self._legs[index].model)
        if kind is FailureKind.FATAL or (
            kind is FailureKind.INVALID and not self._policy.retry_invalid
        ):
            return self._finish(Fail(error))
        return self._advance(now)

    def _finish(self, outcome: Return[T] | Fail) -> list[Action[T]]:
        self._finished = True
        actions: list[Action[T]] = [Abandon(index) for index in self._deadlines]
        self.trail.extend((i, self._legs[i].model, "abandoned") for i in self._deadlines)
        self._deadlines.clear()
        actions.append(outcome)
        return actions

    def _advance(self, now: float) -> list[Action[T]]:
        actions: list[Action[T]] = []
        for index, deadline in list(self._deadlines.items()):
            if now >= deadline:
                del self._deadlines[index]
                actions.append(Abandon(index))
                self._last_error = LegTimeoutError(self._started[index])
                self.trail.append((index, self._started[index].model, "timeout"))
        self._skip_refused()
        while self._may_start(now):
            index = self._next
            self._next += 1
            leg = self._legs[index]
            left = self._budget_ends - now
            if leg.timeout > left:
                leg = Leg(leg.model, left)
            self._started[index] = leg
            self._deadlines[index] = now + leg.timeout
            self._last_start = now
            actions.append(StartLeg(index, leg))
            self._skip_refused()
        if not self._deadlines:
            # Nothing running and nothing allowed to start: out of legs or budget.
            error = self._last_error or RuntimeError("no leg could start")
            return actions + self._finish(Fail(error))
        actions.append(SetTimer(self._next_wake()))
        return actions

    def _skip_refused(self) -> None:
        """Move past legs on a model that has refused this request."""
        while self._next < len(self._legs) and self._legs[self._next].model in self._refused:
            self._next += 1

    def _hedge_at(self) -> float | None:
        """When the next leg may join a running one, if it may at all."""
        hedge_after = self._policy.hedge_after
        if (
            hedge_after is None
            or self._next >= len(self._legs)
            or len(self._deadlines) >= self._policy.max_in_flight
        ):
            return None
        at = self._last_start + hedge_after
        return at if at < self._budget_ends else None

    def _may_start(self, now: float) -> bool:
        if self._next >= len(self._legs) or now >= self._budget_ends:
            return False
        if not self._deadlines:
            return True
        hedge_at = self._hedge_at()
        return hedge_at is not None and now >= hedge_at

    def _next_wake(self) -> float:
        wakes = list(self._deadlines.values())
        hedge_at = self._hedge_at()
        if hedge_at is not None:
            wakes.append(hedge_at)
        return min(wakes)


__all__ = [
    "INTERACTIVE_STALL_AFTER",
    "Abandon",
    "Fail",
    "FailureKind",
    "HedgePolicy",
    "HedgeRun",
    "InvalidResultError",
    "Leg",
    "LegDone",
    "LegFailed",
    "LegTimeoutError",
    "ProviderHealth",
    "Return",
    "SetTimer",
    "StartLeg",
    "TimerFired",
]
