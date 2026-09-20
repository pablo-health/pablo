# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Telling a patient there is something waiting for them in the portal.

One function, because every caller wants the same four decisions made the
same way and none of them is the caller's business:

* **Is there anybody to tell?** A chart with no email address is the
  ordinary case, not an error. The practice still did the thing.
* **Is there anywhere to point them?** A deployment with no portal origin,
  or a practice that has switched its portal off, has no link to send.
* **Is a channel wired at all?** The engine's default sends nothing (see
  :class:`~app.portal.delivery.NoticesNotConfigured`), and that is a
  deployment's choice rather than a failure.
* **What if the send fails?** It is logged and swallowed. The thing that
  happened has already been recorded; failing the clinician's request
  because a mail server was down would lose the act to keep the
  announcement of it.

**What travels is a name and a link.** ``notice`` names one of
:data:`~app.portal.delivery.PORTAL_NOTICES` and the link opens the
practice's portal page, which asks who is holding it before it shows
anything. There is no field here a clinical fact could ride in, which is
the point: an inbox has proved nothing, and everything worth reading is
behind the two factors on the other side of the link.

Nothing here logs the address, the practice or the patient. What is logged
is which notice could not be sent, which is the operational fact.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from ..auth.service import _resolve_practice_from_email
from .delivery import PORTAL_NOTICES, DeliveryNotConfiguredError
from .factory import build_portal_link
from .practice_routes import ensure_practice_slug

if TYPE_CHECKING:
    from .delivery import PortalNoticeDelivery

logger = logging.getLogger(__name__)


def send_portal_notice(
    delivery: PortalNoticeDelivery,
    *,
    notice: str,
    to_email: str | None,
    from_clinician_email: str | None,
) -> bool:
    """Best-effort: tell *to_email* something is waiting. Returns whether it went.

    ``from_clinician_email`` is whose practice the link should point at. It
    is the acting clinician's own address rather than a practice id taken
    from a request, for the same reason the invitation route resolves it
    that way: there is then no field a caller could put another practice in.

    A ``False`` is the ordinary answer on a deployment that has wired no
    channel, and never a reason for the caller to fail.
    """
    if notice not in PORTAL_NOTICES:  # pragma: no cover — callers pass a constant
        raise ValueError(f"unknown portal notice: {notice}")
    if not to_email or not from_clinician_email or not delivery.can_deliver():
        return False

    practice = _resolve_practice_from_email(from_clinician_email)
    if practice is None:
        return False
    address = ensure_practice_slug(practice[0])
    if not address.enabled:
        # The portal is switched off, so the link would open a 404. Saying
        # nothing is better than sending somebody to one.
        return False

    try:
        delivery.send_notice(
            to_email=to_email,
            notice=notice,
            link=build_portal_link(slug=address.slug),
        )
    except DeliveryNotConfiguredError:
        return False
    except Exception:
        logger.exception("portal notice %s could not be delivered", notice)
        return False
    return True


__all__ = ["send_portal_notice"]
