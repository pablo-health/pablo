# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Who in the practice sees a chart the caller cannot.

When an outside record is a colleague's client, the clinician is told so —
"Already a client of the practice, seen by Dr. X" — instead of being offered
a second chart. That line carries the clinicians' names and nothing about
the chart itself: the client's name on screen is the one the outside record
already gave.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..repositories import UserRepository
    from .matching import Candidate


class SeenBy:
    """Display names of the clinicians holding a grant, looked up once each."""

    def __init__(self, users: UserRepository) -> None:
        self._users = users
        self._names: dict[str, str | None] = {}

    def names(self, candidate: Candidate) -> list[str]:
        """The clinicians who see this chart, by the name they go by."""
        return [
            name for name in (self._name(user_id) for user_id in candidate.clinician_ids) if name
        ]

    def _name(self, user_id: str) -> str | None:
        if user_id not in self._names:
            user = self._users.get(user_id)
            self._names[user_id] = user.formal_name if user else None
        return self._names[user_id]


__all__ = ["SeenBy"]
