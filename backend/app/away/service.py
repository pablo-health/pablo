# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Whether a clinician is away, and the acknowledgment a client gets meanwhile.

**Whose period governs.** The client's primary clinician's — the live
``primary`` grant on their chart. A client with no live primary grant is
never acknowledged: there is nobody whose dates could be quoted.

**"Today" is the away clinician's.** Dates are compared on that clinician's
own calendar (their timezone preference, UTC when unset), the same frame
they chose the dates in.

**One per thread per period.** The acknowledgment is a ``practice`` message
carrying the period's id, and a partial unique index on
``(thread_id, away_period_id)`` refuses a second. The insert does nothing on
conflict, so two client messages arriving together still produce one.

**It answers nothing.** It is not a reply: the client's message stays
waiting in the practice's inbox exactly as before, because inbox state is
recorded per client message and a ``practice`` message settles none of them.

:func:`active_or_next_away_period` is the read-only accessor anything else in
a deployment uses for the same dates.
"""

from __future__ import annotations

import uuid
from datetime import UTC
from typing import TYPE_CHECKING, Protocol
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import update
from sqlalchemy.dialects.postgresql import insert

from ..db import get_db_session
from ..db.models import PatientMessageRow, PatientMessageThreadRow
from ..models.patient_message import SENDER_PRACTICE, PatientMessage
from ..portal.clinicians import clinician_name, primary_clinician_id
from ..repositories.postgres.user import PostgresUserRepository
from . import text
from .repository import AwayPeriod, AwayPeriodRepository

if TYPE_CHECKING:
    from datetime import date, datetime, tzinfo

    from sqlalchemy.orm import Session


def clinician_zone(session: Session, user_id: str) -> tzinfo:
    """The clinician's own time zone; UTC when it is unknown."""
    try:
        return ZoneInfo(PostgresUserRepository(session).get_preferences(user_id).timezone)
    except (ZoneInfoNotFoundError, ValueError, AttributeError):
        return UTC


def clinician_today(session: Session, user_id: str, now: datetime) -> date:
    """*now* as a date on the clinician's own calendar; UTC when their zone is unknown."""
    return now.astimezone(clinician_zone(session, user_id)).date()


def active_or_next_away_period(session: Session, user_id: str, now: datetime) -> AwayPeriod | None:
    """The clinician's period in effect at *now*, else their next one, else ``None``.

    Read-only. Use :meth:`AwayPeriod.is_active_on` with :func:`clinician_today`
    to tell the two apart.
    """
    return AwayPeriodRepository(session).active_or_next(
        user_id, clinician_today(session, user_id, now)
    )


class PortalAwayAcknowledger:
    """Acknowledges a client who writes while their clinician is away."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def acknowledge_message(
        self, *, practice_schema: str | None, thread_id: str, patient_id: str, now: datetime
    ) -> PatientMessage | None:
        """Send the acknowledgment into *thread_id*, unless the thread already has one.

        Returns the acknowledgment when this call sent it, ``None`` otherwise.
        """
        period = self._governing_period(practice_schema, patient_id, now)
        if period is None:
            return None
        message = PatientMessage(
            id=str(uuid.uuid4()),
            thread_id=thread_id,
            patient_id=patient_id,
            sender=SENDER_PRACTICE,
            body=text.compose(period.acknowledgment_text),
            created_at=now,
        )
        inserted = self._session.execute(
            insert(PatientMessageRow)
            .values(
                id=message.id,
                thread_id=message.thread_id,
                patient_id=message.patient_id,
                sender=message.sender,
                body=message.body,
                created_at=message.created_at,
                away_period_id=period.id,
            )
            .on_conflict_do_nothing(
                index_elements=["thread_id", "away_period_id"],
                index_where=PatientMessageRow.away_period_id.isnot(None),
            )
            .returning(PatientMessageRow.id)
        ).first()
        if inserted is None:
            return None
        self._session.execute(
            update(PatientMessageThreadRow)
            .where(PatientMessageThreadRow.id == thread_id)
            .values(last_message_at=now)
        )
        self._session.flush()
        return message

    def refill_acknowledgment(
        self, *, practice_schema: str | None, patient_id: str, now: datetime
    ) -> str | None:
        """What a refill request is told while its clinician is away, or ``None``."""
        period = self._governing_period(practice_schema, patient_id, now)
        if period is None:
            return None
        # Only a colleague who accepted is named: one still asked, or who
        # declined, is not covering anything.
        covering = (
            clinician_name(self._session, period.covering_user_id)
            if period.covering_user_id is not None and period.coverage_accepted
            else None
        )
        return text.compose(text.refill_line(period.return_date, covering))

    def _governing_period(
        self, practice_schema: str | None, patient_id: str, now: datetime
    ) -> AwayPeriod | None:
        """The primary clinician's period, if it is in effect and acknowledging."""
        if not practice_schema:
            return None
        clinician_id = primary_clinician_id(self._session, practice_schema, patient_id)
        if clinician_id is None:
            return None
        today = clinician_today(self._session, clinician_id, now)
        period = AwayPeriodRepository(self._session).active_or_next(clinician_id, today)
        if period is None or not period.is_active_on(today) or not period.acknowledgment_enabled:
            return None
        return period


class AwayAcknowledger(Protocol):
    """What the portal routes ask; a test swaps in :class:`NoAwayAcknowledgment`."""

    def acknowledge_message(
        self, *, practice_schema: str | None, thread_id: str, patient_id: str, now: datetime
    ) -> PatientMessage | None: ...

    def refill_acknowledgment(
        self, *, practice_schema: str | None, patient_id: str, now: datetime
    ) -> str | None: ...


class NoAwayAcknowledgment:
    """Nobody is ever away."""

    def acknowledge_message(
        self,
        *,
        practice_schema: str | None,  # noqa: ARG002 — the protocol's signature, unused here
        thread_id: str,  # noqa: ARG002
        patient_id: str,  # noqa: ARG002
        now: datetime,  # noqa: ARG002
    ) -> PatientMessage | None:
        return None

    def refill_acknowledgment(
        self,
        *,
        practice_schema: str | None,  # noqa: ARG002 — the protocol's signature, unused here
        patient_id: str,  # noqa: ARG002
        now: datetime,  # noqa: ARG002
    ) -> str | None:
        return None


def get_away_acknowledger() -> AwayAcknowledger:
    """On the request's own session, which the patient principal has already armed."""
    return PortalAwayAcknowledger(get_db_session())
