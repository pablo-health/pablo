# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Telling a patient a reply is waiting, without telling them twice.

A clinician answering a portal conversation often sends two or three
messages in a row. Each one is not worth an email: the first tells the
patient there is something to read, and the rest are read at the same
sitting. So a notice goes out only when the reply just written is the
patient's *only* unread message from the practice. Until the patient opens
what is waiting, later replies add to it quietly; once they have read it,
the next reply notifies again.

The patient's own read marks (``read_at``, stamped when they open a thread)
are the whole of the state, so there is no notification ledger to keep in
step with the conversation.

Everything else — no address, no portal, no channel, a failed send — is
:func:`~app.portal.notices.send_portal_notice`'s to decide, and none of it
fails the reply.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .notices import send_portal_notice

if TYPE_CHECKING:
    from ..repositories.patient_message import PatientMessageRepository
    from .delivery import PortalNoticeDelivery

#: The notice's name. Says only that something is waiting; see
#: :mod:`app.portal.notices` for why it can be nothing more.
MESSAGE_NOTICE = "portal_message_waiting"


def notify_patient_of_reply(
    delivery: PortalNoticeDelivery,
    messages: PatientMessageRepository,
    *,
    patient_id: str,
    to_email: str | None,
    from_clinician_email: str | None,
) -> bool:
    """Best-effort: email the patient if this reply is all that's waiting.

    Call after the reply is written. Returns whether a notice went.
    """
    if not to_email or not delivery.can_deliver():
        return False
    unread = sum(count for _thread, count in messages.list_patient_threads(patient_id))
    if unread != 1:
        # Zero means the reply isn't visible to the patient as unread (nothing
        # to announce); more than one means an earlier notice is still unread.
        return False
    return send_portal_notice(
        delivery,
        notice=MESSAGE_NOTICE,
        to_email=to_email,
        from_clinician_email=from_clinician_email,
    )


__all__ = ["MESSAGE_NOTICE", "notify_patient_of_reply"]
