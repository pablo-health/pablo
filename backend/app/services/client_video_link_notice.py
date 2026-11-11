# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""The email that gives a client the link to their video session.

Sent when a video appointment becomes real (a clinician books it, a client's
booking is confirmed, a request is agreed to) and again when its time or its
link changes. A cancellation sends nothing from here, and nor does anything
about a session that is already over.

**Whether a link may go out at all** is not decided here: it is
:func:`app.services.telehealth.client_join_link`, which every message to a
client asks, with the practice's own answer from
:func:`app.services.telehealth.practice_sends_client_video_link`. This module
only adds when to send and what the message says.

**Once per change.** What was sent is recorded on the appointment as a digest
of its start time and the link (``client_link_notice``). A save that leaves
both alone finds the same digest and sends nothing, so a retry or a re-save of
some other field never mails the client twice. The digest is written only after
the send succeeds: a send that fails leaves the next save free to try again.

**Only a session still to come.** Once its end has passed, nothing is sent: a
finished session stays confirmed, and an edit to it afterwards (a late note, a
code, a session logged after the fact) must not mail the client a room for it.
A session under way still sends, because the link is still the way in.

**Appointments made before this message existed** have no digest, so the first
change to one that is still ahead sends the "booked" message. It carries the
right time and link, so that is the message they are owed.

**What the message carries.** The practice's name, the date and time in the
practice's zone, the link, and the portal where the practice has one. Never
the client's name (the link has it taken off too), the reason for the visit,
or anything from the chart: an email is read on a lock screen and forwarded.

Best-effort, like every notification on a write path: a send that fails is
logged by ``kind`` and never fails the booking it follows. The address is
PHI-adjacent and is never logged.
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from datetime import UTC
from typing import TYPE_CHECKING

from fastapi import Depends

from ..scheduling_engine.models.appointment import AppointmentStatus
from ..utcnow import utc_now
from .email_sender import EmailSender, OutboundEmail, get_email_sender
from .telehealth import client_join_link

if TYPE_CHECKING:
    from collections.abc import Callable
    from datetime import datetime, tzinfo

    from ..repositories.patient import PatientRepository
    from ..scheduling_engine.models.appointment import Appointment
    from ..scheduling_engine.services.scheduling import SchedulingService

logger = logging.getLogger(__name__)

#: ``OutboundEmail.kind`` for the first message and for an update.
KIND_BOOKED = "video_link_booked"
KIND_CHANGED = "video_link_changed"


@dataclass(frozen=True)
class PracticeFacts:
    """What the message needs to know about the practice."""

    #: The practice's own answer to whether its clients get the link.
    sends_link: bool
    #: The name the practice sends client email under.
    name: str
    #: The practice's portal page, or None where it has no portal on.
    portal_url: str | None = None
    from_address: str | None = None
    reply_to: str | None = None


def read_practice_facts(practice_id: str | None) -> PracticeFacts | None:
    """The practice's facts, on a session of their own. None for no practice.

    Its own session because the caller's may be a patient's or a public one,
    and these are platform rows no tenant context is needed for.
    """
    if not practice_id:
        return None
    from ..db import create_standalone_session
    from ..portal.client_sender import resolve_client_sender
    from ..portal.delivery import DeliveryNotConfiguredError
    from ..portal.factory import build_portal_link
    from ..portal.hosted import practice_slug
    from ..portal.portal_settings import portal_enabled_in
    from .telehealth import practice_sends_client_video_link

    session = create_standalone_session()
    try:
        sends_link = practice_sends_client_video_link(session, practice_id)
        if not sends_link:
            return PracticeFacts(sends_link=False, name="")
        slug = practice_slug(session, practice_id)
        portal_on = slug is not None and portal_enabled_in(session, practice_id)
    finally:
        session.close()
    portal_url: str | None = None
    if slug is not None and portal_on:
        try:
            portal_url = build_portal_link(slug=slug)
        except DeliveryNotConfiguredError:
            portal_url = None
    try:
        sender = resolve_client_sender(practice_id)
    except LookupError:
        return None
    return PracticeFacts(
        sends_link=True,
        name=sender.from_name,
        portal_url=portal_url,
        from_address=sender.from_address,
        reply_to=sender.reply_to,
    )


def practice_id_for_schema(schema: str | None) -> str | None:
    """The id of the practice living in *schema*, or None."""
    if not schema:
        return None
    from sqlalchemy import select

    from ..db import create_standalone_session
    from ..db.platform_models import PracticeRow

    session = create_standalone_session()
    try:
        return session.execute(
            select(PracticeRow.id).where(PracticeRow.schema_name == schema)
        ).scalar_one_or_none()
    finally:
        session.close()


def notice_digest(appointment: Appointment, link: str) -> str:
    """The digest of what a message about *appointment* would tell the client."""
    start = appointment.start_at.astimezone(UTC).isoformat()
    return hashlib.sha256(f"{start}\n{link}".encode()).hexdigest()


def describe_when(start_at: datetime, tz: tzinfo) -> str:
    """``Tuesday, November 18 at 2:00 PM EST``, in the practice's zone."""
    local = start_at.astimezone(tz)
    hour = local.strftime("%I").lstrip("0")
    zone = local.tzname() or ""
    return f"{local:%A}, {local:%B} {local.day} at {hour}:{local:%M %p} {zone}".rstrip()


def compose(
    appointment: Appointment,
    *,
    to: str,
    link: str,
    practice: PracticeFacts,
    tz: tzinfo,
    changed: bool,
) -> OutboundEmail:
    """The message. Built from the practice, the time and the link only."""
    when = describe_when(appointment.start_at, tz)
    if changed:
        subject = f"Your video session with {practice.name} has changed"
        opening = f"Your video session with {practice.name} is now on {when}."
    else:
        subject = f"Your video session with {practice.name}"
        opening = f"Your video session with {practice.name} is on {when}."
    lines = [opening, "", f"Join here: {link}"]
    if practice.portal_url:
        lines += ["", f"You can also join from your portal when it's time: {practice.portal_url}"]
    return OutboundEmail(
        to=to,
        subject=subject,
        text="\n".join(lines),
        kind=KIND_CHANGED if changed else KIND_BOOKED,
        from_name=practice.name,
        from_address=practice.from_address,
        reply_to=practice.reply_to,
    )


@dataclass
class ClientVideoLinkNotifier:
    """Sends the client their video link when it is new to them.

    ``practice_facts`` and ``now`` are seams for tests; production reads the
    platform rows and the clock.
    """

    sender: EmailSender
    practice_facts: Callable[[str | None], PracticeFacts | None] = read_practice_facts
    now: Callable[[], datetime] = utc_now

    def notify(
        self,
        service: SchedulingService,
        appointment: Appointment,
        *,
        practice_id: str | None,
        patients: PatientRepository,
        zone: Callable[[], tzinfo],
    ) -> Appointment:
        """Send if the client has not had this time and link yet. Never raises.

        ``zone`` gives the practice's time zone; it is asked only when a
        message is due.

        Returns the appointment as recorded after the send, or as given when
        nothing was sent.
        """
        try:
            return self._notify(
                service, appointment, practice_id=practice_id, patients=patients, zone=zone
            )
        except Exception:
            logger.exception("client video link notice failed")
            return appointment

    def _notify(
        self,
        service: SchedulingService,
        appointment: Appointment,
        *,
        practice_id: str | None,
        patients: PatientRepository,
        zone: Callable[[], tzinfo],
    ) -> Appointment:
        due = self._due(appointment, practice_id=practice_id, patients=patients, zone=zone)
        if due is None:
            return appointment
        message, digest = due
        try:
            self.sender.send(message)
        except Exception:
            logger.warning("client video link send failed: kind=%s", message.kind)
            return appointment
        return service.update_appointment(
            appointment.id, appointment.user_id, client_link_notice=digest
        )

    def _due(
        self,
        appointment: Appointment,
        *,
        practice_id: str | None,
        patients: PatientRepository,
        zone: Callable[[], tzinfo],
    ) -> tuple[OutboundEmail, str] | None:
        """The message the client is owed and its digest, or None when nothing is."""
        # Asked first, so an appointment that can carry no link reads nothing,
        # and nor does a session already over or a deployment with no mail to
        # send it by.
        if (
            appointment.status != AppointmentStatus.CONFIRMED
            or not appointment.video_link
            or appointment.end_at <= self.now()
            or not self.sender.can_deliver
        ):
            return None
        practice = self.practice_facts(practice_id)
        if practice is None:
            return None
        link = client_join_link(appointment, practice_sends_link=practice.sends_link)
        if link is None:
            return None
        digest = notice_digest(appointment, link)
        if appointment.client_link_notice == digest:
            return None
        patient = patients.get(appointment.patient_id, appointment.user_id)
        if patient is None or not patient.email:
            return None
        message = compose(
            appointment,
            to=str(patient.email),
            link=link,
            practice=practice,
            tz=zone(),
            changed=appointment.client_link_notice is not None,
        )
        return message, digest


def get_client_video_link_notifier(
    sender: EmailSender = Depends(get_email_sender),
) -> ClientVideoLinkNotifier:
    """FastAPI dependency: the notifier over the deployment's email sender."""
    return ClientVideoLinkNotifier(sender=sender)
