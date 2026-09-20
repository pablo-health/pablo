# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Waiting-room webhook receiver — a patient arrived, the call ran, it ended.

A practice on a doxy.me Clinic plan can have its waiting room tell Pablo when
somebody checks in and when the call starts and stops. That is worth having:
a clinician looking at their diary can see that the person is already waiting,
and a no-show is a different conversation from a session nobody logged.

**Off unless a secret is set.** With no shared secret configured there is
nothing to verify a delivery with, so the route answers 404 — not 401, which
would confirm the endpoint exists and invite guessing at it.

Authentication
--------------

The vendor sends the shared secret in a header. It is compared in constant
time before the body is parsed, so a wrong secret cannot be found a character
at a time. Missing or wrong is ``401``; a body that is not a usable event is
``400``.

Idempotency
-----------

Each event writes ONE timestamp column on the appointment, and only when that
column is still empty. A redelivery — and this vendor's retries are the only
delivery guarantee there is — finds the column set and changes nothing, which
is the whole of the dedupe: there is no second ledger to keep in step, and no
window in which two deliveries race to a different answer. The vendor's event
id is required and logged so a delivery can be traced, and it is what
identifies a redelivery in the log; it is not a key this endpoint has to
store, because the column it would protect already refuses a second write.

Which practice
--------------

The delivery carries the opaque handle Pablo put in the room URL and nothing
else, so the appointment is found by that handle, in each practice schema in
turn, on an indexed column. A handle that matches nothing is ``200``: rooms
are used for things that are not Pablo appointments, and a practice's own
traffic must not cost an error.

Logs carry the event id, an outcome token and the handle's length. Never the
handle itself, never a name, never the appointment id.
"""

from __future__ import annotations

import hmac
import json
import logging
from typing import Annotated, Final

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from sqlalchemy import select

from ..auth.route_security import truly_public
from ..db import create_standalone_session, get_engine
from ..db.migrate_tenants import list_active_practice_registry
from ..db.models import AppointmentRow
from ..settings import get_settings
from ..utcnow import utc_now

logger = logging.getLogger(__name__)

router = APIRouter(tags=["telehealth-webhooks"])

TELEHEALTH_WEBHOOK_PATH = "/api/webhooks/telehealth/room"

#: The header the vendor is configured to send the shared secret in. A header
#: NAME, not a credential.
SECRET_HEADER_NAME = "X-Pablo-Room-Secret"  # noqa: S105 — a header name, not a credential

#: What each event kind records. One column per kind, which is also the
#: dedupe: a kind that has already been recorded cannot be recorded again.
_EVENT_COLUMNS: Final[dict[str, str]] = {
    "check_in": "telehealth_checked_in_at",
    "call_start": "telehealth_started_at",
    "call_end": "telehealth_ended_at",
}


@router.post(TELEHEALTH_WEBHOOK_PATH)
async def room_webhook(
    request: Request,
    x_pablo_room_secret: Annotated[str | None, Header()] = None,
    _public: None = Depends(truly_public),
) -> dict[str, str]:
    """Record what the waiting room says happened.

    Public by necessity — the vendor holds no session — and authenticated by
    the shared secret instead.
    """
    configured = get_settings().telehealth_doxy_webhook_secret.get_secret_value()
    if not configured:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "not found")

    if not hmac.compare_digest(x_pablo_room_secret or "", configured):
        logger.warning("room_webhook_secret_invalid header_present=%s", bool(x_pablo_room_secret))
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid webhook secret")

    body = await request.body()
    try:
        payload = json.loads(body)
    except json.JSONDecodeError as exc:
        logger.warning("room_webhook_bad_json err=%s", exc)
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "invalid JSON") from None
    if not isinstance(payload, dict):
        logger.warning("room_webhook_payload_not_object")
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "expected JSON object")

    event_id = str(payload.get("event_id") or "")
    event_type = str(payload.get("event_type") or "")
    handle = str(payload.get("pid") or "")
    if not event_id or not handle:
        logger.warning("room_webhook_unusable type=%s", event_type)
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "missing event_id or pid")

    column = _EVENT_COLUMNS.get(event_type)
    if column is None:
        # An event kind this deployment does not act on. Acknowledged rather
        # than refused: the vendor retries a non-2xx, and a retry loop over an
        # event nobody wants ends with the destination disabled and the real
        # ones going with it.
        logger.info("room_webhook_unhandled event=%s type=%s", event_id, event_type)
        return {"status": "ok"}

    outcome = _record_event(handle, column)
    logger.info(
        "room_webhook_processed event=%s type=%s outcome=%s handle_len=%d",
        event_id,
        event_type,
        outcome,
        len(handle),
    )
    return {"status": "ok", "outcome": outcome}


def _record_event(handle: str, column: str) -> str:
    """Stamp the column on whichever appointment carries this handle.

    ``unmatched`` when no practice has one, ``deduped`` when the column was
    already set — which is what a redelivery looks like — and ``applied``
    when it was written.
    """
    for schema, _practice_id in list_active_practice_registry(get_engine()):
        session = create_standalone_session(schema)
        try:
            row = session.execute(
                select(AppointmentRow).where(AppointmentRow.meeting_external_id == handle)
            ).scalar_one_or_none()
            if row is None:
                continue
            if getattr(row, column) is not None:
                return "deduped"
            setattr(row, column, utc_now())
            session.commit()
            return "applied"
        except Exception:
            session.rollback()
            logger.exception("room_webhook_schema_failed schema=%s", schema)
        finally:
            session.close()
    return "unmatched"
