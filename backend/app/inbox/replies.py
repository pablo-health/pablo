# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""What replying to a client does to their messages in the Inbox.

A reply answers the message it was written to, and that message goes to
Done as ``replied``. The client's earlier messages that are still waiting
stay open: clinicians do not want an older message disappearing because a
newer one was answered. What happens to those instead is the clinician's
standing choice (their ``inbox_reply_earlier_messages`` preference):

* ``ask`` — the default. The reply reports them, and the screen asks once.
* ``always`` — they are marked ``handled`` with the reply, and the reply
  reports which, so the screen can offer to undo it.
* ``never`` — they stay open, and nothing asks.

Handled messages are in Done and can be restored; nothing is deleted.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..models.inbox import (
    DISPOSITION_HANDLED,
    DISPOSITION_REPLIED,
    KIND_PORTAL_MESSAGE,
    EarlierMessagesOnReply,
    ReplyInboxOutcome,
)
from .service import UnknownInboxItemError
from .sources._ids import uuids

if TYPE_CHECKING:
    from ..models.patient_message import InboxMessage
    from ..repositories import InboxItemStateRepository, PatientMessageRepository
    from .registry import InboxContext

#: How far back one client's waiting messages are read. More than any one
#: client leaves unanswered.
CLIENT_AWAITING_LIMIT = 200


def _earlier(awaiting: list[InboxMessage], than: InboxMessage) -> list[InboxMessage]:
    return [
        row
        for row in awaiting
        if row.message.id != than.message.id and row.message.created_at < than.message.created_at
    ]


def _handle(
    ctx: InboxContext, states: InboxItemStateRepository, rows: list[InboxMessage]
) -> list[str]:
    for row in rows:
        states.record(
            ctx.user_id, (KIND_PORTAL_MESSAGE, row.message.id), DISPOSITION_HANDLED, ctx.now
        )
    return [row.message.id for row in rows]


def replied_to(
    ctx: InboxContext,
    messages: PatientMessageRepository,
    *,
    thread_id: str,
    patient_id: str,
    in_reply_to: str | None,
) -> InboxMessage | None:
    """The client message a reply answers, if it answers one.

    Named by the caller when it knows (the Inbox always does). Otherwise the
    newest message still waiting in the thread, which is what a reply typed
    into the conversation is answering. ``None`` when nothing is waiting.
    Raises :class:`UnknownInboxItemError` for a named message that is not a
    client message in this thread.
    """
    if in_reply_to is not None:
        found = messages.get_inbox_messages(ctx.user_id, uuids([in_reply_to]))
        if not found or found[0].thread.id != thread_id:
            raise UnknownInboxItemError(f"{KIND_PORTAL_MESSAGE}:{in_reply_to}")
        return found[0]
    awaiting = messages.list_awaiting_messages(
        ctx.user_id, now=ctx.now, patient_id=patient_id, limit=CLIENT_AWAITING_LIMIT
    )
    return next((row for row in awaiting if row.thread.id == thread_id), None)


def settle_reply(
    ctx: InboxContext,
    states: InboxItemStateRepository,
    messages: PatientMessageRepository,
    target: InboxMessage | None,
    preference: EarlierMessagesOnReply,
) -> ReplyInboxOutcome:
    """Mark *target* replied, and treat the client's earlier ones per *preference*."""
    if target is None:
        return ReplyInboxOutcome()
    states.record(
        ctx.user_id, (KIND_PORTAL_MESSAGE, target.message.id), DISPOSITION_REPLIED, ctx.now
    )
    outcome = ReplyInboxOutcome(resolved_ids=[target.message.id])
    if preference == "never":
        return outcome
    earlier = _earlier(
        messages.list_awaiting_messages(
            ctx.user_id,
            now=ctx.now,
            patient_id=target.thread.patient_id,
            limit=CLIENT_AWAITING_LIMIT,
        ),
        target,
    )
    if preference == "always":
        outcome.earlier_handled_ids = _handle(ctx, states, earlier)
    else:
        outcome.earlier_open_ids = [row.message.id for row in earlier]
    return outcome


def handle_earlier(
    ctx: InboxContext,
    states: InboxItemStateRepository,
    messages: PatientMessageRepository,
    message_id: str,
) -> tuple[str, list[str]]:
    """Mark handled every message from this client still waiting from before *message_id*.

    The "Yes" to the question a reply asks. Returns the client's patient id
    and the ids it marked. Raises :class:`UnknownInboxItemError` for a
    message the clinician cannot see.
    """
    found = messages.get_inbox_messages(ctx.user_id, uuids([message_id]))
    if not found:
        raise UnknownInboxItemError(f"{KIND_PORTAL_MESSAGE}:{message_id}")
    awaiting = messages.list_awaiting_messages(
        ctx.user_id,
        now=ctx.now,
        patient_id=found[0].thread.patient_id,
        limit=CLIENT_AWAITING_LIMIT,
    )
    return found[0].thread.patient_id, _handle(ctx, states, _earlier(awaiting, found[0]))
