# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Request and response shapes for the clinician's away-period routes."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import TYPE_CHECKING, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .text import SAFETY_LINES

if TYPE_CHECKING:
    from .repository import AwayPeriod

MAX_ACKNOWLEDGMENT_TEXT = 2000

CoverageStatus = Literal["requested", "accepted", "declined"]


def default_return_date(end_date: date) -> date:
    """The day after the last day away, when the clinician names no other."""
    return end_date + timedelta(days=1)


class AwayDates(BaseModel):
    """First and last day away, both inclusive, and the day the clinician is back."""

    model_config = ConfigDict(extra="forbid")

    start_date: date
    end_date: date
    #: Defaults to the day after ``end_date``.
    return_date: date | None = None
    covering_user_id: str | None = None

    @model_validator(mode="after")
    def _ordered(self) -> Self:
        if self.end_date < self.start_date:
            raise ValueError("The last day away can't be before the first.")
        if self.return_date is not None and self.return_date <= self.end_date:
            raise ValueError("The day you're back has to be after the last day away.")
        return self

    @property
    def resolved_return_date(self) -> date:
        return self.return_date or default_return_date(self.end_date)


class CreateAwayPeriodRequest(AwayDates):
    acknowledgment_enabled: bool = True
    #: The body the clinician reviewed. The safety lines are added on send.
    acknowledgment_text: str = Field(min_length=1, max_length=MAX_ACKNOWLEDGMENT_TEXT)


class UpdateAwayPeriodRequest(BaseModel):
    """Only the fields sent change.

    Changing the dates or the covering colleague changes what the stored
    text says is true, so such a change must carry the text again — the
    clinician reviews it against the new dates rather than the old text
    going out with a date it no longer matches.
    """

    model_config = ConfigDict(extra="forbid")

    start_date: date | None = None
    end_date: date | None = None
    return_date: date | None = None
    covering_user_id: str | None = None
    acknowledgment_enabled: bool | None = None
    acknowledgment_text: str | None = Field(
        default=None, min_length=1, max_length=MAX_ACKNOWLEDGMENT_TEXT
    )


class AcknowledgmentPreviewRequest(AwayDates):
    pass


class AcknowledgmentPreviewResponse(BaseModel):
    #: The suggested body, for the clinician to review and edit.
    acknowledgment_text: str
    #: Added after the body on every acknowledgment. Not editable.
    safety_lines: str = SAFETY_LINES
    return_date: date


class CoveringOption(BaseModel):
    user_id: str
    name: str


class AwayPeriodResponse(BaseModel):
    id: str
    start_date: date
    end_date: date
    return_date: date
    covering_user_id: str | None
    covering_name: str | None
    #: ``requested``, ``accepted`` or ``declined``; ``None`` when nobody is named.
    coverage_status: CoverageStatus | None
    acknowledgment_enabled: bool
    acknowledgment_text: str
    safety_lines: str = SAFETY_LINES
    #: Away today, on the clinician's calendar.
    active: bool
    ended_at: datetime | None

    @classmethod
    def from_period(
        cls, period: AwayPeriod, *, today: date, covering_name: str | None
    ) -> AwayPeriodResponse:
        return cls(
            id=period.id,
            start_date=period.start_date,
            end_date=period.end_date,
            return_date=period.return_date,
            covering_user_id=period.covering_user_id,
            covering_name=covering_name,
            coverage_status=period.coverage_status,  # type: ignore[arg-type]  # CHECK-constrained
            acknowledgment_enabled=period.acknowledgment_enabled,
            acknowledgment_text=period.acknowledgment_text,
            active=period.is_active_on(today),
            ended_at=period.ended_at,
        )


class AwayPeriodListResponse(BaseModel):
    #: Current and upcoming periods, earliest first.
    periods: list[AwayPeriodResponse]
    #: Covered periods that are over, ending in the last 30 days, latest
    #: first: where the clinician finds what their colleague did.
    recently_covered: list[AwayPeriodResponse] = Field(default_factory=list)
    #: Colleagues in the practice who may be named as covering.
    covering_options: list[CoveringOption]


class CoverageActivityItem(BaseModel):
    at: datetime
    #: An audit action, e.g. ``patient_viewed`` or ``note_updated``.
    action: str
    resource_type: str
    resource_id: str
    patient_id: str
    client_name: str | None


class CoverageActivityResponse(BaseModel):
    """What the covering colleague did on the clinician's clients, newest first."""

    covering_name: str | None
    items: list[CoverageActivityItem]


class CoverageRequestResponse(BaseModel):
    """An away period, as seen by the colleague asked to cover it."""

    period_id: str
    clinician_id: str
    clinician_name: str | None
    start_date: date
    end_date: date
    return_date: date
    status: CoverageStatus
    #: The clinician is away today, on their own calendar.
    active: bool

    @classmethod
    def from_period(
        cls, period: AwayPeriod, *, today: date, clinician_name: str | None
    ) -> CoverageRequestResponse:
        return cls(
            period_id=period.id,
            clinician_id=period.user_id,
            clinician_name=clinician_name,
            start_date=period.start_date,
            end_date=period.end_date,
            return_date=period.return_date,
            status=period.coverage_status,  # type: ignore[arg-type]  # CHECK-constrained
            active=period.is_active_on(today),
        )


class CoverageRequestListResponse(BaseModel):
    #: Periods the caller is asked to cover, not ended and not past, earliest first.
    requests: list[CoverageRequestResponse]
