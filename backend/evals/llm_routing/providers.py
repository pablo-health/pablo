# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Fake model providers for the routing simulator, on a virtual clock.

A provider call is decided in full when it starts: how long it takes and
what comes back. Latency is a lognormal body (median 1.6 s, p95 about
6 s, the shape the availability parser sees on a healthy day) plus, now
and then, a stall drawn log-uniformly from 15-94 s, which is the range
production stalls have actually spanned.

Stalls come in bursts. Each provider lives through alternating calm and
stormy episodes (exponential durations), and stalls far more often in a
storm. ``rho`` couples a second provider to the first: at ``rho=0`` the
two weather storms independently; at ``rho=1`` they storm together and a
call that stalls on one stalls on the other, which is the case hedging
cannot help.
"""

from __future__ import annotations

import bisect
import math
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import random
    from collections.abc import Mapping

_Z95 = 1.6449

STALL_RANGE = (15.0, 94.0)


class Outcome(Enum):
    OK = "ok"
    WRONG = "wrong"
    """Schema-valid and plausible, but not what the sentence meant. Only the
    oracle can tell; no validation catches it."""
    TRUNCATED = "truncated"
    """An answer cut off mid-object. It arrives, and validation rejects it."""
    INVALID = "invalid"
    """Not JSON at all. The gateway raises, as it does in production."""
    ERROR_504 = "504"
    ERROR_429 = "429"


@dataclass(frozen=True)
class ProviderProfile:
    median: float = 1.6
    p95: float = 6.0
    stall_calm: float = 0.002
    stall_storm: float = 0.0
    outage_in_storm: bool = False
    """Every call in a storm is a fast 504: the provider is down, not slow."""
    p_504: float = 0.002
    seconds_to_504: float = 10.0
    p_429: float = 0.002
    p_truncated: float = 0.001
    p_invalid: float = 0.001
    p_wrong: float = 0.01
    cost: float = 1.0
    samples: tuple[float, ...] = ()
    """Observed latencies to replay in place of the lognormal body and the
    stalls. See ``replay.py``."""

    @property
    def sigma(self) -> float:
        return math.log(self.p95 / self.median) / _Z95


@dataclass(frozen=True)
class Call:
    duration: float
    outcome: Outcome
    cost: float


@dataclass(frozen=True)
class Episodes:
    """Stormy intervals over the simulated horizon, sorted."""

    starts: tuple[float, ...] = ()
    ends: tuple[float, ...] = ()

    def stormy(self, t: float) -> bool:
        i = bisect.bisect_right(self.starts, t) - 1
        return i >= 0 and t < self.ends[i]

    @classmethod
    def generate(
        cls, rng: random.Random, horizon: float, calm_mean: float, storm_mean: float
    ) -> Episodes:
        if storm_mean <= 0:
            return cls()
        starts: list[float] = []
        ends: list[float] = []
        t = rng.expovariate(1 / calm_mean)
        while t < horizon:
            starts.append(t)
            t += rng.expovariate(1 / storm_mean)
            ends.append(t)
            t += rng.expovariate(1 / calm_mean)
        return cls(tuple(starts), tuple(ends))


def _draw(profile: ProviderProfile, rng: random.Random, *, stormy: bool, u: float) -> Call:
    """One call's fate. ``u`` decides the stall, so two coupled providers
    handed the same ``u`` stall together."""
    if stormy and profile.outage_in_storm:
        return Call(0.3, Outcome.ERROR_504, 0.0)
    r = rng.random()
    if r < profile.p_429:
        return Call(0.05, Outcome.ERROR_429, 0.0)
    r -= profile.p_429
    if r < profile.p_504:
        return Call(profile.seconds_to_504, Outcome.ERROR_504, 0.0)

    if profile.samples:
        duration = profile.samples[rng.randrange(len(profile.samples))]
    elif u < (profile.stall_storm if stormy else profile.stall_calm):
        duration = math.exp(rng.uniform(math.log(STALL_RANGE[0]), math.log(STALL_RANGE[1])))
    else:
        duration = rng.lognormvariate(math.log(profile.median), profile.sigma)

    r = rng.random()
    outcome = Outcome.OK
    for p, kind in (
        (profile.p_truncated, Outcome.TRUNCATED),
        (profile.p_invalid, Outcome.INVALID),
        (profile.p_wrong, Outcome.WRONG),
    ):
        if r < p:
            outcome = kind
            break
        r -= p
    return Call(duration, outcome, profile.cost)


@dataclass
class Fleet:
    """The providers a scenario routes across, keyed by model name.

    The first is the reference provider; every other one follows its
    weather with probability ``rho`` and its own otherwise.
    """

    profiles: Mapping[str, ProviderProfile]
    weather: Mapping[str, Episodes]
    rho: float = 0.0
    _reference: str = field(init=False)

    def __post_init__(self) -> None:
        self._reference = next(iter(self.profiles))

    def call(self, model: str, t: float, request: RequestDraws, rng: random.Random) -> Call:
        """Start a call on ``model`` at time ``t``, for one request.

        A coupled provider reuses the stall draw of the request's latest
        call to the reference provider, so it stalls when that call did.
        A retry on the same provider draws afresh: stalls are per call.
        """
        profile = self.profiles[model]
        if model == self._reference:
            request.reference_u = rng.random()
            return _draw(profile, rng, stormy=self.weather[model].stormy(t), u=request.reference_u)
        coupled = rng.random() < self.rho
        weather = self.weather[self._reference if coupled else model]
        u = request.reference_u if coupled and request.reference_u is not None else rng.random()
        return _draw(profile, rng, stormy=weather.stormy(t), u=u)


@dataclass
class RequestDraws:
    """What one request's calls share."""

    reference_u: float | None = None
