# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""The provider conditions the routing policy is proven against.

Every scenario has a primary and a secondary provider. Each is run twice:
under today's policy (the primary alone, one retry) and under a hedged
one (primary, secondary, then each again). Both policies are built by
:meth:`HedgePolicy.for_models` with the production bounds, so the
simulator and the gateway cannot drift apart.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.reliability.hedge import HedgePolicy

from .providers import ProviderProfile

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
