# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Replay observed latencies through the routing policy.

The fake providers' lognormal-plus-stalls shape is a model. A trace of
real call latencies (from the parse result log's ``latency_ms``, or an
eval run) checks the policy against what a provider actually did: each
call draws its duration from the trace instead of the model.

A trace is a file of latencies in seconds, either a JSON list or one
number per line.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from .providers import ProviderProfile
from .scenarios import Scenario

if TYPE_CHECKING:
    from pathlib import Path


def load_trace(path: Path) -> tuple[float, ...]:
    text = path.read_text().strip()
    values = json.loads(text) if text.startswith("[") else text.split()
    trace = tuple(float(v) for v in values)
    if not trace:
        raise ValueError(f"{path} holds no latencies")
    return trace


def replay_scenario(trace: tuple[float, ...], secondary: ProviderProfile | None = None) -> Scenario:
    """The primary replays ``trace``; the secondary is healthy unless given."""
    return Scenario(
        "replay",
        f"The primary replays {len(trace)} observed latencies.",
        primary=ProviderProfile(samples=trace),
        secondary=secondary or ProviderProfile(),
    )
