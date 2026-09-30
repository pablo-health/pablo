# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Refill requests still waiting on a prescriber's answer.

Answered on the Refills page, which is where the item opens; once answered
the request is no longer ``requested`` and leaves the Inbox on its own.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ...models.inbox import KIND_REFILL, InboxItem
from ...repositories import get_refill_request_repository
from ._ids import uuids

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable

    from ...models.refill_request import RefillQueueEntry
    from ...repositories import RefillRequestRepository
    from ..registry import InboxContext


def _item(entry: RefillQueueEntry) -> InboxItem:
    request = entry.request
    first = entry.patient_preferred_name or entry.patient_first_name
    return InboxItem(
        kind=KIND_REFILL,
        source_id=request.id,
        patient_id=request.patient_id,
        patient_name=f"{first} {entry.patient_last_name}".strip(),
        title=f"Refill request: {request.medication_text}",
        detail=request.patient_note,
        occurred_at=request.created_at,
        href="/dashboard/refills",
    )


class RefillSource:
    kind = KIND_REFILL

    def __init__(
        self, repo: Callable[[], RefillRequestRepository] = get_refill_request_repository
    ) -> None:
        self._repo = repo

    def list_open(self, ctx: InboxContext) -> list[InboxItem]:
        return [_item(entry) for entry in self._repo().list_queue(ctx.user_id, "pending")]

    def get_items(self, ctx: InboxContext, source_ids: Iterable[str]) -> list[InboxItem]:
        repo = self._repo()
        return [
            _item(entry)
            for request_id in uuids(source_ids)
            if (entry := repo.get_entry(request_id, ctx.user_id)) is not None
        ]
