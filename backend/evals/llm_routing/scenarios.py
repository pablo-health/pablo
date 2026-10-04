# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""The provider conditions the routing policy is proven against.

Every scenario has a primary and a secondary provider. Each is run under
the old sequential policy (the primary alone, one retry after it), the
interactive policy the gateway now uses (the same retry, started beside a
stalled primary), and a hedged one with the secondary as a fallback. All
are built by :class:`HedgePolicy` with the production bounds, so the
simulator and the gateway cannot drift apart.

:data:`STORIES` pin single requests down call by call, for the cases a
sampled scenario only shows in a tail.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from app.reliability.hedge import INTERACTIVE_STALL_AFTER, HedgePolicy

from .providers import ProviderProfile, Script, error_504, ok, stall

if TYPE_CHECKING:
    from .providers import Call

PRIMARY = "primary"
SECONDARY = "secondary"

#: The bounds the gateway uses: each attempt 15 s, no new leg after 25 s.
ATTEMPT_TIMEOUT = 15.0
BUDGET = 25.0

#: The hedge delay scenarios run with unless told otherwise. Chosen from
#: the sweep in ``run.py``: the healthy body's p95 is 6 s, so hedging much
#: earlier pays for a second call on one request in ten or more.
DEFAULT_HEDGE_AFTER = 6.0


@dataclass(frozen=True)
class Scenario:
    name: str
    description: str
    primary: ProviderProfile = field(default_factory=ProviderProfile)
    secondary: ProviderProfile = field(default_factory=ProviderProfile)
    rho: float = 0.0
    calm_mean: float = 1800.0
    storm_mean: float = 0.0
    """Mean stormy-episode length in seconds; 0 means always calm."""

    spacing: float = 3.0
    """Seconds between request arrivals, so bursts span many requests."""


def baseline_policy() -> HedgePolicy:
    """Today: the primary, then one retry on it, one after the other."""
    return HedgePolicy.for_models(PRIMARY, attempt_timeout=ATTEMPT_TIMEOUT, budget=BUDGET)


def hedged_policy(hedge_after: float | None = DEFAULT_HEDGE_AFTER) -> HedgePolicy:
    return HedgePolicy.for_models(
        PRIMARY,
        (SECONDARY,),
        attempt_timeout=ATTEMPT_TIMEOUT,
        budget=BUDGET,
        hedge_after=hedge_after,
    )


def interactive_policy(
    fallbacks: tuple[str, ...] = (), stall_after: float | None = INTERACTIVE_STALL_AFTER
) -> HedgePolicy:
    """What the gateway serves an interactive call with now.

    With no fallbacks, the primary and one retry on it, the retry starting
    beside a stalled primary rather than after it.
    """
    return HedgePolicy.interactive(
        PRIMARY,
        fallbacks,
        attempt_timeout=ATTEMPT_TIMEOUT,
        budget=BUDGET,
        stall_after=stall_after,
    )


@dataclass(frozen=True)
class Story:
    """One request's calls, written out, to pin a single case down."""

    name: str
    description: str
    primary: tuple[Call, ...]
    secondary: tuple[Call, ...] = ()

    def script(self) -> Script:
        return Script({PRIMARY: self.primary, SECONDARY: self.secondary})


#: Single requests told call by call. The first is what was seen on a real
#: parse: the primary stalled to its 15 s bound and its retry took 6.9 s.
STORIES: tuple[Story, ...] = (
    Story(
        "stall_then_slow_retry",
        "The primary stalls; its retry answers in 6.9 s.",
        primary=(stall(), ok(6.9)),
        secondary=(ok(1.6),),
    ),
    Story(
        "errors_at_once",
        "The primary fails in 0.3 s, then answers normally.",
        primary=(error_504(0.3), ok(1.6)),
        secondary=(ok(1.6),),
    ),
    Story(
        "errors_twice",
        "The primary fails in 0.3 s, twice.",
        primary=(error_504(0.3), error_504(0.3)),
        secondary=(ok(1.6),),
    ),
    Story(
        "stalls",
        "The primary stalls past every bound; a retry answers normally.",
        primary=(stall(), ok(1.6)),
        secondary=(ok(1.6),),
    ),
    Story(
        "errors_after_10s",
        "The primary fails after 10 s, then answers normally.",
        primary=(error_504(10.0), ok(1.6)),
        secondary=(ok(1.6),),
    ),
    Story(
        "everything_fails",
        "Every call fails in 0.3 s.",
        primary=(error_504(0.3), error_504(0.3)),
        secondary=(error_504(0.3),),
    ),
    Story(
        "everything_stalls",
        "Every call stalls.",
        primary=(stall(), stall()),
        secondary=(stall(),),
    ),
)


_BURSTY = ProviderProfile(stall_calm=0.005, stall_storm=0.35)

SCENARIOS: tuple[Scenario, ...] = (
    Scenario("healthy", "Both providers healthy: rare stalls, rare errors."),
    Scenario(
        "single_stalls",
        "The primary stalls on 3% of calls, independently of each other.",
        primary=ProviderProfile(stall_calm=0.03),
    ),
    Scenario(
        "bursty",
        "The primary has stormy spells (mean 5 min every 20) stalling 35% of calls.",
        primary=_BURSTY,
        calm_mean=1200.0,
        storm_mean=300.0,
    ),
    Scenario(
        "primary_outage",
        "The primary is down and answers every call with a fast 504.",
        primary=ProviderProfile(outage_in_storm=True),
        # One storm, from the first instant to well past the last request.
        calm_mean=1e-9,
        storm_mean=1e12,
    ),
    Scenario(
        "slow_secondary",
        "The secondary is slow (median 5 s, 5% stalls): hedging must do no harm.",
        primary=ProviderProfile(stall_calm=0.01),
        secondary=ProviderProfile(median=5.0, p95=15.0, stall_calm=0.05),
    ),
    Scenario(
        "both_degraded_rho0",
        "Both providers bursty, weathering storms independently.",
        primary=_BURSTY,
        secondary=_BURSTY,
        rho=0.0,
        calm_mean=1200.0,
        storm_mean=300.0,
    ),
    Scenario(
        "flapping",
        "The primary drops out for ~30 s at a time, every minute or so.",
        primary=ProviderProfile(outage_in_storm=True),
        calm_mean=60.0,
        storm_mean=30.0,
    ),
    Scenario(
        "both_degraded_rho1",
        "Both bursty and fully coupled: they stall together. Hedging cannot help.",
        primary=_BURSTY,
        secondary=_BURSTY,
        rho=1.0,
        calm_mean=1200.0,
        storm_mean=300.0,
    ),
)

BY_NAME = {s.name: s for s in SCENARIOS}
