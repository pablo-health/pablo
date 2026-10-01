# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Client messages: one Inbox item per message, never one per conversation.

Three messages from a client are three items. Replying answers the message
replied to and nothing else, so a clinician never finds an older message
gone without having handled it (see :mod:`app.inbox.replies`). A message
waits while its conversation is open; closing the conversation is handling
it where it lives.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ...models.inbox import KIND_PORTAL_MESSAGE, InboxItem
from ...repositories import get_patient_message_repository
from ._ids import uuids

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable

    from ...models.patient_message import InboxMessage
    from ...repositories import PatientMessageRepository
    from ..registry import InboxContext

#: The most messages one read lists. Handled ones are left out in SQL, so
#: this bounds what is still waiting, which a practice keeps small.
AWAITING_LIMIT = 200


def message_item(row: InboxMessage) -> InboxItem:
    return InboxItem(
        kind=KIND_PORTAL_MESSAGE,
        source_id=row.message.id,
        patient_id=row.thread.patient_id,
        patient_name=row.patient_name,
        title=row.thread.subject or "Message",
        detail=row.message.body,
        occurred_at=row.message.created_at,
        href=f"/dashboard/inbox?filter=messages&item={row.message.id}",
        context={"thread_id": row.thread.id},
    )


class PortalMessageSource:
    kind = KIND_PORTAL_MESSAGE

    def __init__(
        self, repo: Callable[[], PatientMessageRepository] = get_patient_message_repository
    ) -> None:
        self._repo = repo

    def list_open(self, ctx: InboxContext) -> list[InboxItem]:
        rows = self._repo().list_awaiting_messages(ctx.user_id, now=ctx.now, limit=AWAITING_LIMIT)
        return [message_item(row) for row in rows]

    def get_items(self, ctx: InboxContext, source_ids: Iterable[str]) -> list[InboxItem]:
        rows = self._repo().get_inbox_messages(ctx.user_id, uuids(source_ids))
        return [message_item(row) for row in rows]
