# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Source ids arrive from a URL, and every built-in source keys on a uuid."""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterable


def uuids(ids: Iterable[str]) -> list[str]:
    """The ids that are uuids, deduplicated, in order.

    Anything else names no row, and handing it to a uuid column would be a
    database error rather than the "not found" it is.
    """
    kept: list[str] = []
    for value in ids:
        try:
            uuid.UUID(value)
        except ValueError:
            continue
        if value not in kept:
            kept.append(value)
    return kept
