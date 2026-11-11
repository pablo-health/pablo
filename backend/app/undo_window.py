# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""The soft-delete undo window, shared by every restorable record.

A deleted patient (THERAPY-yg2) and a deleted session (PABLO-ocvu) can be
restored for the same 30 days, after which the day-30 hard-purge cron
(THERAPY-cgy) physically removes the rows. One constant, so the two
restore paths and the two "Recently deleted" listings can never drift.
Kept here rather than in settings to make the contract obvious at the call
site and impossible to widen via env vars.
"""

from enum import StrEnum
from typing import Final

#: Days a soft-deleted row stays restorable. Sized to match the soft-delete
#: retention before the hard-purge cron removes the row.
UNDO_WINDOW_DAYS: Final = 30


class IncludeDeletedMode(StrEnum):
    """Soft-delete visibility for a listing route.

    Default behavior (``include_deleted`` omitted) lists only live rows —
    soft-deleted rows are hidden by the repository's ``deleted_at IS NULL``
    filter (THERAPY-nyb).

    ``recent`` switches the listing to the undo window: rows with
    ``deleted_at IS NOT NULL AND deleted_at > NOW() - UNDO_WINDOW_DAYS``.
    The hard-purge cron physically removes anything older, so this mode
    never returns rows that are about to vanish.
    """

    RECENT = "recent"
