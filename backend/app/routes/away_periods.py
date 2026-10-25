# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""The caller's away dates, and the acknowledgment their clients get meanwhile.

  ``/api/away-periods``

    GET    ""                   -> current and upcoming periods, and who may cover
    POST   /preview             -> the suggested acknowledgment for these dates
    POST   ""                   -> set away dates
    PATCH  /{period_id}         -> change them
    POST   /{period_id}/end     -> end a period early

**Each clinician's dates are their own.** Every write names the caller, so a
period that is somebody else's is "not found". A covering colleague must be a
clinician of the same practice; naming one grants them nothing.

**The text is reviewed before it is used.** Setting dates takes the
acknowledgment text with them; changing the dates or the covering colleague
takes it again, so a client is never told a date the clinician did not see in
the text. The safety lines are added on send and are not part of what is
stored.

Not audited: away dates name no client and carry nothing clinical.
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, Depends, status
from pydantic import ValidationError

from ..api_errors import ConflictError, NotFoundError, UnprocessableEntityError
from ..auth.service import TenantContext, get_tenant_context, require_baa_acceptance
from ..away.repository import AwayPeriod, AwayPeriodRepository
from ..away.schemas import (
    AcknowledgmentPreviewRequest,
    AcknowledgmentPreviewResponse,
    AwayDates,
    AwayPeriodListResponse,
    AwayPeriodResponse,
    CoveringOption,
    CreateAwayPeriodRequest,
    UpdateAwayPeriodRequest,
)
from ..away.service import clinician_today
from ..away.text import default_body
from ..db import get_db_session
from ..models import User  # noqa: TC001 — FastAPI resolves dependency annotations at runtime
from ..portal.clinicians import clinician_name, practice_clinicians
from ..utcnow import utc_now

if TYPE_CHECKING:
    from datetime import date

    from sqlalchemy.orm import Session

router = APIRouter(prefix="/api/away-periods", tags=["away-periods"])

NOT_FOUND_MESSAGE = "These away dates were not found."
OVERLAP_MESSAGE = "You already have away dates that overlap these."
COVERING_MESSAGE = "Choose a colleague in your practice to cover."
REVIEW_TEXT_MESSAGE = "Review the reply for the new dates before saving."

#: Changing any of these changes what the stored text says is true.
_TEXT_BEARING = ("start_date", "end_date", "return_date", "covering_user_id")


class _Context:
    """The request's session, the caller, and their practice."""

    def __init__(self, ctx: TenantContext, user: User) -> None:
        self.session: Session = get_db_session()
        self.user = user
        self.schema = str(ctx.practice_schema or "")
        self.repo = AwayPeriodRepository(self.session)

    @property
    def today(self) -> date:
        return clinician_today(self.session, self.user.id, utc_now())

    def colleagues(self) -> list[CoveringOption]:
        if not self.schema:
            return []
        options = []
        for user_id in practice_clinicians(self.session, self.schema):
            if user_id == self.user.id:
                continue
            name = clinician_name(self.session, user_id)
            if name:
                options.append(CoveringOption(user_id=user_id, name=name))
        return sorted(options, key=lambda option: option.name.lower())

    def covering_name(self, covering_user_id: str | None) -> str | None:
        if covering_user_id is None:
            return None
        return clinician_name(self.session, covering_user_id)

    def require_colleague(self, covering_user_id: str | None) -> None:
        if covering_user_id is None:
            return
        if covering_user_id not in {option.user_id for option in self.colleagues()}:
            raise UnprocessableEntityError(COVERING_MESSAGE, code="COVERING_NOT_A_COLLEAGUE")

    def respond(self, period: AwayPeriod) -> AwayPeriodResponse:
        return AwayPeriodResponse.from_period(
            period, today=self.today, covering_name=self.covering_name(period.covering_user_id)
        )


def get_away_context(
    ctx: TenantContext = Depends(get_tenant_context),
    user: User = Depends(require_baa_acceptance),
) -> _Context:
    return _Context(ctx, user)


@router.get("", response_model=AwayPeriodListResponse)
def list_away_periods(away: _Context = Depends(get_away_context)) -> AwayPeriodListResponse:
    """The caller's current and upcoming periods, and the colleagues who may cover."""
    periods = away.repo.list_current_and_upcoming(away.user.id, away.today)
    return AwayPeriodListResponse(
        periods=[away.respond(p) for p in periods], covering_options=away.colleagues()
    )


@router.post("/preview", response_model=AcknowledgmentPreviewResponse)
def preview_acknowledgment(
    body: AcknowledgmentPreviewRequest, away: _Context = Depends(get_away_context)
) -> AcknowledgmentPreviewResponse:
    """The suggested acknowledgment for these dates, for the caller to review."""
    away.require_colleague(body.covering_user_id)
    return AcknowledgmentPreviewResponse(
        acknowledgment_text=default_body(
            body.resolved_return_date,
            clinician_name(away.session, away.user.id),
            away.covering_name(body.covering_user_id),
        ),
        return_date=body.resolved_return_date,
    )


@router.post("", status_code=status.HTTP_201_CREATED, response_model=AwayPeriodResponse)
def create_away_period(
    body: CreateAwayPeriodRequest, away: _Context = Depends(get_away_context)
) -> AwayPeriodResponse:
    """Set away dates. The acknowledgment is on unless the caller turned it off."""
    away.require_colleague(body.covering_user_id)
    if away.repo.overlapping(away.user.id, body.start_date, body.end_date):
        raise ConflictError(OVERLAP_MESSAGE)
    now = utc_now()
    period = away.repo.add(
        AwayPeriod(
            id=str(uuid.uuid4()),
            user_id=away.user.id,
            start_date=body.start_date,
            end_date=body.end_date,
            return_date=body.resolved_return_date,
            covering_user_id=body.covering_user_id,
            acknowledgment_enabled=body.acknowledgment_enabled,
            acknowledgment_text=body.acknowledgment_text.strip(),
            ended_at=None,
            created_at=now,
            updated_at=now,
        )
    )
    return away.respond(period)


@router.patch("/{period_id}", response_model=AwayPeriodResponse)
def update_away_period(
    period_id: uuid.UUID,
    body: UpdateAwayPeriodRequest,
    away: _Context = Depends(get_away_context),
) -> AwayPeriodResponse:
    """Change the caller's own period. New dates come with the text reviewed for them."""
    current = away.repo.get(str(period_id), away.user.id)
    if current is None or current.ended_at is not None:
        raise NotFoundError(NOT_FOUND_MESSAGE)

    sent = body.model_dump(exclude_unset=True)
    changes: dict[str, Any] = {k: v for k, v in sent.items() if getattr(current, k) != v}
    if any(field in changes for field in _TEXT_BEARING) and "acknowledgment_text" not in sent:
        raise UnprocessableEntityError(REVIEW_TEXT_MESSAGE, code="REVIEW_ACKNOWLEDGMENT_TEXT")

    dates = _merged_dates(current, sent)
    if "end_date" in changes and "return_date" not in sent:
        changes["return_date"] = dates.resolved_return_date
    if "covering_user_id" in changes:
        away.require_colleague(dates.covering_user_id)
    if ("start_date" in changes or "end_date" in changes) and away.repo.overlapping(
        away.user.id, dates.start_date, dates.end_date, excluding=current.id
    ):
        raise ConflictError(OVERLAP_MESSAGE)
    if "acknowledgment_text" in changes:
        changes["acknowledgment_text"] = changes["acknowledgment_text"].strip()

    updated = away.repo.update(current.id, away.user.id, changes, utc_now())
    if updated is None:
        raise NotFoundError(NOT_FOUND_MESSAGE)
    return away.respond(updated)


@router.post("/{period_id}/end", response_model=AwayPeriodResponse)
def end_away_period(
    period_id: uuid.UUID, away: _Context = Depends(get_away_context)
) -> AwayPeriodResponse:
    """End the caller's own period now. Nothing is acknowledged for it afterwards."""
    ended = away.repo.end(str(period_id), away.user.id, utc_now())
    if ended is None:
        raise NotFoundError(NOT_FOUND_MESSAGE)
    return away.respond(ended)


def _merged_dates(current: AwayPeriod, sent: dict[str, Any]) -> AwayDates:
    """The period's dates after this change, checked the way a new one is."""
    end_date = sent.get("end_date", current.end_date)
    keeps_return = "end_date" not in sent or end_date == current.end_date
    try:
        return AwayDates(
            start_date=sent.get("start_date", current.start_date),
            end_date=end_date,
            return_date=sent.get("return_date", current.return_date if keeps_return else None),
            covering_user_id=sent.get("covering_user_id", current.covering_user_id),
        )
    except ValidationError as exc:
        message = exc.errors()[0]["msg"].removeprefix("Value error, ")
        raise UnprocessableEntityError(message, code="AWAY_DATES_INVALID") from exc
