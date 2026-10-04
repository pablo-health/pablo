# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Report how the routing policy behaves across every scenario.

Prints, for each scenario, the old sequential policy (one model, a retry
only after the first attempt has ended) beside the interactive one the
gateway uses now and the interactive one with a fallback configured; then
the single requests in ``STORIES`` under each; then a sweep of the stall
threshold against p99 and the share of requests that pay for a second
call. No network, no credentials, and the same numbers every time for a
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

from app.reliability.hedge import INTERACTIVE_STALL_AFTER

from .replay import load_trace, replay_scenario
from .scenarios import (
    SCENARIOS,
    SECONDARY,
    STORIES,
    baseline_policy,
    interactive_policy,
)
from .sim import simulate, simulate_script

if TYPE_CHECKING:
    from app.reliability.hedge import HedgePolicy

    from .scenarios import Scenario
    from .sim import Report

SWEEP_DELAYS: tuple[float | None, ...] = (2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 10.0, 12.0, None)
SWEEP_SCENARIOS = ("healthy", "single_stalls", "bursty", "both_degraded_rho0")

_HEADER = (
    f"{'scenario':<20} {'policy':<11} {'p50':>6} {'p95':>6} {'p99':>6} {'max':>6} "
    f"{'>budget':>8} {'@budget':>8} {'hedged':>7} {'2x-bill':>8} {'cost':>5} {'wrong':>6} "
    f"{'failed':>7}"
)


def _policies(stall_after: float) -> tuple[tuple[str, HedgePolicy], ...]:
    return (
        ("before", baseline_policy()),
        ("interactive", interactive_policy(stall_after=stall_after)),
        ("+fallback", interactive_policy((SECONDARY,), stall_after=stall_after)),
    )


def _row(name: str, label: str, r: Report) -> str:
    return (
        f"{name:<20} {label:<11} {r.p50:>6.2f} {r.p95:>6.2f} {r.p99:>6.2f} {r.max:>6.1f} "
        f"{r.over_budget:>8.2%} {r.at_budget:>8.2%} {r.hedge_rate:>7.2%} {r.double_billed:>8.2%} "
        f"{r.cost_per_request:>5.2f} {r.wrong_rate:>6.2%} {r.failure_rate:>7.2%}"
    )


def _compare(
    scenarios: tuple[Scenario, ...], stall_after: float, n: int, seed: int
) -> list[dict[str, object]]:
    print(f"Sequential retry beside the interactive policy, stall after {stall_after:g}s")
    print(f"({n} requests each)\n")
    print(_HEADER)
    rows: list[dict[str, object]] = []
    for scenario in scenarios:
        for label, policy in _policies(stall_after):
            report = simulate(scenario, policy, n=n, seed=seed)
            print(_row(scenario.name, label, report))
            rows.append({"scenario": scenario.name, "policy": label, **asdict(report)})
    return rows


def _stories(stall_after: float) -> list[dict[str, object]]:
    print("\nSingle requests: seconds until answered (or failed)\n")
    policies = _policies(stall_after)
    print(f"{'story':<24}" + "".join(f"{label:>16}" for label, _ in policies))
    rows: list[dict[str, object]] = []
    for story in STORIES:
        cells = []
        for label, policy in policies:
            record = simulate_script(policy, story.script())
            cells.append(f"{record.wall:>6.1f}s {record.result:<7}".rjust(16))
            rows.append(
                {"story": story.name, "policy": label, "wall": record.wall, "result": record.result}
            )
        print(f"{story.name:<24}" + "".join(cells))
    return rows


def _sweep(n: int, seed: int) -> list[dict[str, object]]:
    print("\nStall threshold sweep, one model: p99 seconds / share of requests hedged\n")
    by_name = {s.name: s for s in SCENARIOS}
    print(f"{'stall':>6}  " + "  ".join(f"{name:>20}" for name in SWEEP_SCENARIOS))
    rows: list[dict[str, object]] = []
    for delay in SWEEP_DELAYS:
        cells = []
        for name in SWEEP_SCENARIOS:
            report = simulate(by_name[name], interactive_policy(stall_after=delay), n=n, seed=seed)
            cells.append(f"{report.p99:>6.2f}s / {report.hedge_rate:>6.2%}".rjust(20))
            rows.append({"scenario": name, "stall_after": delay, **asdict(report)})
        label = "never" if delay is None else f"{delay:g}s"
        print(f"{label:>6}  " + "  ".join(cells))
    return rows


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--n", type=int, default=10_000, help="requests per scenario")
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--hedge-after", type=float, default=INTERACTIVE_STALL_AFTER)
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
        results["stories"] = _stories(args.hedge_after)
        results["sweep"] = _sweep(args.n, args.seed)
    if args.json:
        print(json.dumps(results, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
