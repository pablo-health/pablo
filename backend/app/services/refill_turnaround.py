# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""When a refill request is due: the practice's own target, on its working days.

The practice sets how many working days it gives itself to answer
(:attr:`~app.portal.portal_settings.PortalSettings.refill_turnaround_days`),
and each request is stamped with the end of the last of those days when it
arrives. Stamped once, not recomputed: a request should not become more or
less urgent because somebody edited their hours after it came in.

**It is the practice's target, not a promise.** The due time orders the
practice's queue. No patient-facing response, confirmation or notice carries
it, and nothing here is reachable from one.

**Whose working days.** The practice owner's, read the same way patient
booking reads them and for the same reason: a patient principal cannot be
asked who their clinician is (``patient_clinicians`` is gated on the
clinician's own session), and for the single-clinician practice the owner is
the prescriber. A separate session is armed as the owner for the read, so the
request's own patient-armed session never sees a clinician's rules.

A practice with no owner, or an owner with no hours set, counts Monday to
Friday in UTC — a request still gets a due time rather than none.
"""

from __future__ import annotations

import logging
from datetime import UTC
from typing import TYPE_CHECKING, Protocol
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from ..db import arm_current_user_id, create_standalone_session, set_tenant_schema
from ..db.platform_models import PracticeRow
from ..portal.portal_settings import portal_settings_for_schema
from ..repositories.postgres.availability_rule import PostgresAvailabilityRuleRepository
from ..repositories.postgres.user import PostgresUserRepository
from .working_days import WorkingCalendar, due_at

if TYPE_CHECKING:
    from datetime import date, datetime, tzinfo

    from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)


class RefillDueClock(Protocol):
    def __call__(self, practice_schema: str, submitted_at: datetime) -> datetime:
        """When a request arriving at *submitted_at* is due, for this practice."""


def _owner_and_zone(session: Session, practice_schema: str) -> tuple[str | None, tzinfo]:
    """The practice owner and their time zone; UTC when either is unknown."""
    owner = (
        session.query(PracticeRow.owner_user_id)
        .filter(PracticeRow.schema_name == practice_schema)
        .scalar()
    )
    if not owner:
        return None, UTC
    try:
        return owner, ZoneInfo(PostgresUserRepository(session).get_preferences(owner).timezone)
    except (ZoneInfoNotFoundError, ValueError, AttributeError):
        # The raw preference is caller-controlled; only the id is logged.
        logger.warning("refill turnaround: unresolvable timezone for owner %s, using UTC", owner)
        return owner, UTC


def practice_calendar(practice_schema: str) -> tuple[WorkingCalendar, tzinfo]:
    """The practice owner's working calendar and zone; Monday to Friday in UTC without one.

    Shared by every target the practice counts in working days (a refill's
    turnaround, a message's reply time), so they all fall on the same days.
    """
    session = create_standalone_session()
    try:
        owner, tz = _owner_and_zone(session, practice_schema)
        if owner is None:
            return WorkingCalendar(), UTC
        set_tenant_schema(session, practice_schema)
        arm_current_user_id(session, owner)
        rules = PostgresAvailabilityRuleRepository(session).list_by_user(owner)
        return WorkingCalendar.from_rules(rules), tz
    finally:
        session.close()


def practice_refill_due_at(practice_schema: str, submitted_at: datetime) -> datetime:
    """The due time for a request arriving now, on the practice's own calendar."""
    days = portal_settings_for_schema(practice_schema).refill_turnaround_days
    calendar, tz = practice_calendar(practice_schema)
    return due_at(submitted_at, days, calendar, tz)


def get_refill_due_clock() -> RefillDueClock:
    return practice_refill_due_at


class PracticeToday(Protocol):
    def __call__(self, practice_schema: str, moment: datetime) -> date:
        """The practice's calendar date at *moment*."""


def practice_today(practice_schema: str, moment: datetime) -> date:
    """The date at *moment* in the practice owner's zone, the frame due days are counted in.

    A client's "days left" is counted from this day, so a request sent late
    in the evening is not read as a day later than the client meant.
    """
    session = create_standalone_session()
    try:
        _owner, tz = _owner_and_zone(session, practice_schema)
    finally:
        session.close()
    return moment.astimezone(tz).date()


def get_practice_today() -> PracticeToday:
    return practice_today
