# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Coverage, from the colleague's side: the away dates they are asked to cover.

  ``/api/covering``

    GET    ""                     -> periods the caller is asked to cover
    POST   /{period_id}/accept    -> cover them
    POST   /{period_id}/decline   -> don't, or stop

**Only the colleague named answers.** A period that does not name the caller
is "not found". Accepting grants the caller the away clinician's clients for
exactly the away dates (:mod:`app.away.coverage`); declining writes the
decline and nothing else, and after an acceptance it ends that access at once.

Audited: the answer, one row on the period; and each client whose access it
grants or ends, one row per client. The list names colleagues and dates, no
client, and is not.
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from fastapi import APIRouter, Depends, Request

from ..api_errors import ConflictError, NotFoundError
from ..auth.service import require_baa_acceptance
from ..away.coverage import (
    CoverageConflict,
    accept_coverage,
    decline_coverage,
    window_changes,
)
from ..away.repository import (
    COVERAGE_ACCEPTED,
    COVERAGE_DECLINED,
    AwayPeriod,
    AwayPeriodRepository,
)
from ..away.schemas import CoverageRequestListResponse, CoverageRequestResponse
from ..away.service import clinician_today
from ..db import get_db_session
from ..models import AuditAction, User
from ..portal.clinicians import clinician_name
from ..services import AuditService, get_audit_service
from ..utcnow import utc_now

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

router = APIRouter(prefix="/api/covering", tags=["covering"])

NOT_FOUND_MESSAGE = "This coverage request was not found."
ALREADY_COVERING_MESSAGE = (
    "You're already covering for this colleague on other dates. "
    "You can accept these once that coverage is over."
)


def _respond(session: Session, period: AwayPeriod) -> CoverageRequestResponse:
    return CoverageRequestResponse.from_period(
        period,
        today=clinician_today(session, period.user_id, utc_now()),
        clinician_name=clinician_name(session, period.user_id),
    )


def _open_request(session: Session, period_id: uuid.UUID, user: User) -> AwayPeriod:
    """A period naming the caller that is neither ended nor already over."""
    period = AwayPeriodRepository(session).get_asked_of(str(period_id), user.id)
    if period is None or period.ended_at is not None:
        raise NotFoundError(NOT_FOUND_MESSAGE)
    if period.return_date <= clinician_today(session, period.user_id, utc_now()):
        raise NotFoundError(NOT_FOUND_MESSAGE)
    return period


@router.get("", response_model=CoverageRequestListResponse)
def list_coverage_requests(
    user: User = Depends(require_baa_acceptance),
) -> CoverageRequestListResponse:
    """The periods the caller is asked to cover, not ended and not past, earliest first."""
    session = get_db_session()
    today = clinician_today(session, user.id, utc_now())
    periods = AwayPeriodRepository(session).asked_of(user.id, today)
    return CoverageRequestListResponse(requests=[_respond(session, p) for p in periods])


@router.post("/{period_id}/accept", response_model=CoverageRequestResponse)
def accept_coverage_request(
    period_id: uuid.UUID,
    request: Request,
    user: User = Depends(require_baa_acceptance),
    audit: AuditService = Depends(get_audit_service),
) -> CoverageRequestResponse:
    """Cover these dates. Accepting twice changes nothing."""
    session = get_db_session()
    period = _open_request(session, period_id, user)
    if period.coverage_status == COVERAGE_ACCEPTED:
        return _respond(session, period)
    now = utc_now()
    try:
        granted = accept_coverage(session, period, now)
    except CoverageConflict as exc:
        raise ConflictError(ALREADY_COVERING_MESSAGE) from exc
    audit.log_coverage_action(
        AuditAction.COVERAGE_ACCEPTED,
        actor_id=user.id,
        request=request,
        away_period_id=period.id,
        changes={"client_count": len(granted)},
    )
    audit.log_coverage_action(
        AuditAction.COVERAGE_ACCESS_GRANTED,
        actor_id=user.id,
        request=request,
        away_period_id=period.id,
        patient_ids=granted,
        changes=window_changes(session, period),
        only_with_patients=True,
    )
    return _respond(session, _reread(session, period, user))


@router.post("/{period_id}/decline", response_model=CoverageRequestResponse)
def decline_coverage_request(
    period_id: uuid.UUID,
    request: Request,
    user: User = Depends(require_baa_acceptance),
    audit: AuditService = Depends(get_audit_service),
) -> CoverageRequestResponse:
    """Don't cover these dates, or stop covering them. Declining twice changes nothing."""
    session = get_db_session()
    period = _open_request(session, period_id, user)
    if period.coverage_status == COVERAGE_DECLINED:
        return _respond(session, period)
    now = utc_now()
    ended = decline_coverage(session, period, now)
    audit.log_coverage_action(
        AuditAction.COVERAGE_ACCESS_ENDED,
        actor_id=user.id,
        request=request,
        away_period_id=period.id,
        patient_ids=ended,
        changes={"ended_at": now.isoformat(), "ended_by": "covering_clinician"},
        only_with_patients=True,
    )
    audit.log_coverage_action(
        AuditAction.COVERAGE_DECLINED,
        actor_id=user.id,
        request=request,
        away_period_id=period.id,
        changes={"client_count": len(ended)},
    )
    return _respond(session, _reread(session, period, user))


def _reread(session: Session, period: AwayPeriod, user: User) -> AwayPeriod:
    fresh = AwayPeriodRepository(session).get_asked_of(period.id, user.id)
    if fresh is None:
        raise NotFoundError(NOT_FOUND_MESSAGE)
    return fresh
