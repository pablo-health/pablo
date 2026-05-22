# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.
"""Phase 1.4 chat scorers (THERAPY-j39e).

Each scorer is a callable ``(*, output, expected, **kwargs) -> dict``.
Scorers return ``{"score": None}`` for cases that don't apply to them
so Braintrust's aggregation skips them rather than counting as zero.
"""

from .instruction_holding import instruction_holding_scorer
from .no_confabulation import no_confabulation_scorer
from .refusal import refusal_scorer

__all__ = [
    "instruction_holding_scorer",
    "no_confabulation_scorer",
    "refusal_scorer",
]
