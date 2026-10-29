# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Where a floating block sits on one date, as the engine resolves it."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from datetime import date, datetime


@dataclass(frozen=True)
class ResolvedFloatingBlock:
    """One floating block on one date.

    ``state`` is a :class:`~app.scheduling_engine.services.floating_block.BlockState`
    value. ``start`` and ``end`` are aware UTC instants, None when the block
    is skipped that day or no free stretch of its length is left.
    """

    rule_id: str
    label: str
    date: date
    state: str
    duration_minutes: int
    window_start: str
    window_end: str
    start: datetime | None = None
    end: datetime | None = None
