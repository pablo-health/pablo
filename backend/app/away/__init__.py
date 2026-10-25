# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Clinician away periods and the out-of-office acknowledgment.

* :mod:`.text` — the words: the reviewed body, the fixed safety lines, the
  refill line.
* :mod:`.repository` — the ``clinician_away_periods`` rows.
* :mod:`.service` — "is this clinician away", and acknowledging a client
  who writes through the portal meanwhile.
* :mod:`.schemas` — the request and response models for ``/api/away-periods``.
"""

from .repository import AwayPeriod, AwayPeriodRepository
from .service import PortalAwayAcknowledger, active_or_next_away_period, clinician_today

__all__ = [
    "AwayPeriod",
    "AwayPeriodRepository",
    "PortalAwayAcknowledger",
    "active_or_next_away_period",
    "clinician_today",
]
