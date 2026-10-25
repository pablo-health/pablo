# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Client messages: one Inbox item per message, never one per conversation.

Three messages from a client are three items. Replying answers the message
replied to and nothing else, so a clinician never finds an older message
gone without having handled it (see :mod:`app.inbox.replies`). A message
waits while its conversation is open; closing the conversation is handling
it where it lives.

Each message carries when the practice means to have answered it by
(``due_at``, :mod:`app.services.message_reply_target`), so the Inbox shows the
day and puts the overdue ones first.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ...models.inbox import KIND_PORTAL_MESSAGE, InboxItem
from ...repositories import get_patient_message_repository
from ...services.message_reply_target import practice_reply_due
from ._ids import uuids

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable
    from datetime import datetime

    from ...models.patient_message import InboxMessage
    from ...repositories import PatientMessageRepository
    from ...services.message_reply_target import ReplyDueClock
    from ..registry import InboxContext

#: The most messages one read lists. Handled ones are left out in SQL, so
#: this bounds what is still waiting, which a practice keeps small.
AWAITING_LIMIT = 200


def message_item(row: InboxMessage, due: Callable[[datetime], datetime] | None = None) -> InboxItem:
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
        due_at=due(row.message.created_at) if due is not None else None,
    )


class PortalMessageSource:
    kind = KIND_PORTAL_MESSAGE

    def __init__(
        self,
        repo: Callable[[], PatientMessageRepository] = get_patient_message_repository,
        reply_due: ReplyDueClock = practice_reply_due,
        schema: Callable[[], str | None] | None = None,
    ) -> None:
        self._repo = repo
        self._reply_due = reply_due
        self._schema = schema

    def _due(self, repo: PatientMessageRepository) -> Callable[[datetime], datetime] | None:
        """The practice's reply-by clock, for the practice the rows were read from."""
        schema = self._schema() if self._schema is not None else repo.practice_schema()
        return self._reply_due(schema) if schema is not None else None

    def list_open(self, ctx: InboxContext) -> list[InboxItem]:
        repo = self._repo()
        rows = repo.list_awaiting_messages(ctx.user_id, now=ctx.now, limit=AWAITING_LIMIT)
        if not rows:
            return []
        due = self._due(repo)
        return [message_item(row, due) for row in rows]

    def get_items(self, ctx: InboxContext, source_ids: Iterable[str]) -> list[InboxItem]:
        repo = self._repo()
        rows = repo.get_inbox_messages(ctx.user_id, uuids(source_ids))
        if not rows:
            return []
        due = self._due(repo)
        return [message_item(row, due) for row in rows]
