# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""The Inbox's own shapes: an item, what was done with it, and the API around them.

An :class:`InboxItem` is a view over a row in its source's table — a refill,
a client message, an appointment — never a copy of it. An
:class:`InboxItemState` is the one thing the Inbox itself remembers about an
item: that a clinician dismissed it, snoozed it, marked it handled or replied.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

# The kinds this engine lists. A deployment may register more; nothing here
# enumerates them, so a new kind needs no change to this module.
KIND_PORTAL_MESSAGE = "portal_message"
KIND_REFILL = "refill"
KIND_INTAKE_REVIEW = "intake_review"
KIND_NOTE_TO_SIGN = "note_to_sign"
KIND_CALENDAR_CHANGE = "calendar_change"

# What this engine writes into ``inbox_item_states.disposition``. Open-ended:
# see :func:`hides` for how any value, known here or not, is read.
DISPOSITION_REPLIED = "replied"
DISPOSITION_HANDLED = "handled"
DISPOSITION_DISMISSED = "dismissed"
DISPOSITION_SNOOZED = "snoozed"
DISPOSITION_RESTORED = "restored"

InboxView = Literal["open", "done"]
Severity = Literal["normal", "urgent"]

#: What a clinician chose for their earlier, still-open messages from a
#: client when they reply to that client. Stored in their preferences.
EarlierMessagesOnReply = Literal["ask", "always", "never"]


@dataclass(frozen=True)
class InboxItemState:
    """The live row of ``inbox_item_states`` for one clinician and one item."""

    id: str
    user_id: str
    source_kind: str
    source_id: str
    disposition: str
    resolved_at: datetime
    resolved_by: str | None = None
    snoozed_until: datetime | None = None


def hides(state: InboxItemState, now: datetime) -> bool:
    """Whether this state keeps its item out of the Open view.

    Every disposition hides its item except a restore, and a snooze whose
    time has passed. Written as the exceptions rather than a list of what
    hides, so a disposition a deployment records itself is read as handled
    without this engine having to know its name.
    """
    if state.disposition == DISPOSITION_RESTORED:
        return False
    if state.disposition == DISPOSITION_SNOOZED:
        return state.snoozed_until is not None and state.snoozed_until > now
    return True


class InboxItem(BaseModel):
    """One thing that needs the clinician, as the Inbox lists it.

    ``source_id`` is the row id in the source's own table. ``href`` is where
    "Open" goes: the place the item is acted on, which is never the Inbox
    re-implementing the action. ``context`` carries what one kind's card
    needs beyond the common fields (a thread id, an appointment's status),
    as strings, so the shape stays the same for every kind.

    The last three fields are filled from :class:`InboxItemState` for an item
    the clinician has handled, and are empty on an open one.
    """

    kind: str
    source_id: str
    patient_id: str | None = None
    patient_name: str | None = None
    title: str
    detail: str | None = None
    occurred_at: datetime
    severity: Severity = "normal"
    href: str
    context: dict[str, str] = Field(default_factory=dict)
    disposition: str | None = None
    resolved_at: datetime | None = None
    snoozed_until: datetime | None = None


class InboxListResponse(BaseModel):
    data: list[InboxItem]
    total: int


class InboxCountResponse(BaseModel):
    count: int


class SnoozeInboxItemRequest(BaseModel):
    until: datetime


class HandledEarlierResponse(BaseModel):
    handled_ids: list[str]


class ReplyInboxOutcome(BaseModel):
    """What replying to a client did to their messages in the Inbox.

    ``resolved_ids`` are the messages the reply itself answered.
    ``earlier_open_ids`` are the client's earlier messages still open, when
    the clinician has asked to be asked about them; ``earlier_handled_ids``
    are the ones marked handled because the clinician asked for that every
    time. At most one of the two is non-empty.
    """

    resolved_ids: list[str] = Field(default_factory=list)
    earlier_open_ids: list[str] = Field(default_factory=list)
    earlier_handled_ids: list[str] = Field(default_factory=list)
