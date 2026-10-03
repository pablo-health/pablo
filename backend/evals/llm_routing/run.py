# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Report how the routing policy behaves across every scenario.

Prints, for each scenario, today's policy (one model, one retry) beside
the hedged one; then a sweep of the hedge delay against p99 and hedge
rate. No network, no credentials, and the same numbers every time for a
given seed.

    cd backend && poetry run python -m evals.llm_routing.run
    cd backend && poetry run python -m evals.llm_routing.run --hedge-after 5 --n 20000
    cd backend && poetry run python -m evals.llm_routing.run --trace latencies.json
"""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from pathlib import Path
from typing import TYPE_CHECKING

from .replay import load_trace, replay_scenario
from .scenarios import DEFAULT_HEDGE_AFTER, SCENARIOS, baseline_policy, hedged_policy
from .sim import simulate

if TYPE_CHECKING:
    from .scenarios import Scenario
    from .sim import Report

SWEEP_DELAYS: tuple[float | None, ...] = (2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 10.0, 12.0, None)
SWEEP_SCENARIOS = ("healthy", "single_stalls", "bursty", "both_degraded_rho0")

_HEADER = (
    f"{'scenario':<20} {'policy':<9} {'p50':>6} {'p95':>6} {'p99':>6} {'max':>6} "
    f"{'>budget':>8} {'hedged':>7} {'2x-bill':>8} {'cost':>5} {'wrong':>6} {'failed':>7}"
)


def _row(name: str, label: str, r: Report) -> str:
    return (
        f"{name:<20} {label:<9} {r.p50:>6.2f} {r.p95:>6.2f} {r.p99:>6.2f} {r.max:>6.1f} "
        f"{r.over_budget:>8.2%} {r.hedge_rate:>7.2%} {r.double_billed:>8.2%} "
        f"{r.cost_per_request:>5.2f} {r.wrong_rate:>6.2%} {r.failure_rate:>7.2%}"
    )


def _compare(
    scenarios: tuple[Scenario, ...], hedge_after: float, n: int, seed: int
) -> list[dict[str, object]]:
    print(f"Today's policy beside hedging after {hedge_after:g}s ({n} requests each)\n")
    print(_HEADER)
    rows: list[dict[str, object]] = []
    for scenario in scenarios:
        for label, policy in (
            ("today", baseline_policy()),
            ("hedged", hedged_policy(hedge_after)),
        ):
            report = simulate(scenario, policy, n=n, seed=seed)
            print(_row(scenario.name, label, report))
            rows.append({"scenario": scenario.name, "policy": label, **asdict(report)})
    return rows


def _sweep(n: int, seed: int) -> list[dict[str, object]]:
    print("\nHedge delay sweep: p99 seconds / share of requests hedged\n")
    by_name = {s.name: s for s in SCENARIOS}
    print(f"{'delay':>6}  " + "  ".join(f"{name:>20}" for name in SWEEP_SCENARIOS))
    rows: list[dict[str, object]] = []
    for delay in SWEEP_DELAYS:
        cells = []
        for name in SWEEP_SCENARIOS:
            report = simulate(by_name[name], hedged_policy(delay), n=n, seed=seed)
            cells.append(f"{report.p99:>6.2f}s / {report.hedge_rate:>6.2%}".rjust(20))
            rows.append({"scenario": name, "hedge_after": delay, **asdict(report)})
        label = "never" if delay is None else f"{delay:g}s"
        print(f"{label:>6}  " + "  ".join(cells))
    return rows


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--n", type=int, default=10_000, help="requests per scenario")
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--hedge-after", type=float, default=DEFAULT_HEDGE_AFTER)
    parser.add_argument("--trace", type=Path, help="replay these latencies as the primary")
    parser.add_argument(
        "--scenario", action="append", choices=[s.name for s in SCENARIOS], help="only these"
    )
    parser.add_argument("--json", action="store_true", help="also print the results as JSON")
    args = parser.parse_args(argv)

    if args.trace is not None:
        scenarios: tuple[Scenario, ...] = (replay_scenario(load_trace(args.trace)),)
    else:
        scenarios = tuple(s for s in SCENARIOS if not args.scenario or s.name in args.scenario)
    results = {"compare": _compare(scenarios, args.hedge_after, args.n, args.seed)}
    if args.trace is None and not args.scenario:
        results["sweep"] = _sweep(args.n, args.seed)
    if args.json:
        print(json.dumps(results, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
