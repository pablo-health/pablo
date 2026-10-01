# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Intake forms a client handed in, waiting for the practice to review.

Reviewed on the client's chart. Accepting the form, or asking for a
correction, moves it out of ``submitted`` and out of the Inbox.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

from ...models.inbox import KIND_INTAKE_REVIEW, InboxItem
from ...repositories import get_patient_intake_assignment_repository
from ._ids import uuids

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable
    from datetime import datetime

    from ...repositories import PatientIntakeAssignmentRepository
    from ..registry import InboxContext


def _item(row: dict[str, object]) -> InboxItem:
    patient_id = str(row["patient_id"])
    # Set on every submitted row; an entry read for the Done view after the
    # form moved on still has the time it last changed.
    submitted = cast("datetime", row.get("submitted_at") or row["updated_at"])
    form = str(row.get("form_name") or "") or "Intake form"
    return InboxItem(
        kind=KIND_INTAKE_REVIEW,
        source_id=str(row["id"]),
        patient_id=patient_id,
        patient_name=str(row.get("patient_name") or "") or None,
        title=f"Form to review: {form}",
        occurred_at=submitted,
        href=f"/dashboard/patients/{patient_id}",
        context={"assignment_id": str(row["id"])},
    )


class IntakeReviewSource:
    kind = KIND_INTAKE_REVIEW

    def __init__(
        self,
        repo: Callable[
            [], PatientIntakeAssignmentRepository
        ] = get_patient_intake_assignment_repository,
    ) -> None:
        self._repo = repo

    def list_open(self, ctx: InboxContext) -> list[InboxItem]:
        return [_item(row) for row in self._repo().list_awaiting_review(ctx.user_id)]

    def get_items(self, ctx: InboxContext, source_ids: Iterable[str]) -> list[InboxItem]:
        return [
            _item(row) for row in self._repo().get_review_entries(ctx.user_id, uuids(source_ids))
        ]
