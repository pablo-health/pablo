# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Homework: suggested on a note, assigned by the clinician, done by the client.

**Pablo never assigns.** A note's plan is read for homework in the
same step as its chart updates (:mod:`.proposals`), and the suggestions are
decided with them at signing; only an accepted one becomes an assignment
(``HomeworkFamily.apply``). What is here is the assignments themselves.

Assignment status moves:

    assigned -> done (the client) | withdrawn (the clinician)
    done     -> assigned (the client, "not done after all") | withdrawn
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from ..utcnow import utc_now
from .repository import CLIENT_STATUSES

if TYPE_CHECKING:
    from .repository import Assignment, HomeworkRepository


class HomeworkDecisionError(Exception):
    """The assignment is not in a status this change applies to (any more)."""


class HomeworkNotFoundError(Exception):
    pass


@dataclass(frozen=True)
class Overview:
    #: Assigned, done and withdrawn, newest first.
    assignments: list[Assignment]


class HomeworkService:
    def __init__(self, repo: HomeworkRepository) -> None:
        self._repo = repo

    def overview(self, patient_id: str) -> Overview:
        return Overview(assignments=self._repo.list_for_patient(patient_id))

    # --- the clinician's own changes ---

    def add(self, patient_id: str, text: str, user_id: str) -> Assignment:
        return self._repo.add(patient_id, text, user_id, utc_now())

    def withdraw(self, patient_id: str, assignment_id: str, user_id: str) -> Assignment:
        if self._repo.get(assignment_id, patient_id) is None:
            raise HomeworkNotFoundError(assignment_id)
        now = utc_now()
        moved = self._repo.transition(
            assignment_id,
            patient_id,
            from_statuses=CLIENT_STATUSES,
            values={
                "status": "withdrawn",
                "withdrawn_by": user_id,
                "withdrawn_at": now,
                "updated_at": now,
            },
        )
        if moved is None:
            raise HomeworkDecisionError(assignment_id)
        return moved

    def edit(self, patient_id: str, assignment_id: str, text: str) -> Assignment:
        """Reword an assignment the client already has. Not once it is done or withdrawn."""
        if self._repo.get(assignment_id, patient_id) is None:
            raise HomeworkNotFoundError(assignment_id)
        moved = self._repo.transition(
            assignment_id,
            patient_id,
            from_statuses=("assigned",),
            values={"body": text, "updated_at": utc_now()},
        )
        if moved is None:
            raise HomeworkDecisionError(assignment_id)
        return moved

    # --- the client's side ---

    def list_for_client(self, patient_id: str) -> list[Assignment]:
        return self._repo.list_for_client(patient_id)

    def mark(
        self, patient_id: str, assignment_id: str, *, done: bool, note: str | None = None
    ) -> Assignment:
        """The client marks an assignment done (with an optional note), or not done after all."""
        now = utc_now()
        if done:
            values: dict[str, object] = {
                "status": "done",
                "completed_at": now,
                "client_note": note,
                "updated_at": now,
            }
        else:
            values = {
                "status": "assigned",
                "completed_at": None,
                "client_note": None,
                "updated_at": now,
            }
        moved = self._repo.transition(
            assignment_id,
            patient_id,
            from_statuses=("assigned",) if done else ("done",),
            values=values,
        )
        if moved is None:
            # A withdrawn one is not the client's any more: it answers the same
            # as an id that does not exist.
            current = self._repo.get(assignment_id, patient_id)
            if current is None or current.status not in CLIENT_STATUSES:
                raise HomeworkNotFoundError(assignment_id)
            raise HomeworkDecisionError(assignment_id)
        return moved
