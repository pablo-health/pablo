# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Request and response shapes for the clinician's waitlist routes."""

from __future__ import annotations

from datetime import datetime  # noqa: TC003 — pydantic resolves field types at runtime
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, ConfigDict, Field

if TYPE_CHECKING:
    from .repository import WaitlistEntry

Weekday = Literal["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
DayPart = Literal["morning", "afternoon", "evening"]
Modality = Literal["in_person", "telehealth", "either"]
VisitModality = Literal["in_person", "telehealth"]

MAX_SESSION_TYPE = 30


class AddWaitlistEntryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    days: list[Weekday] = Field(min_length=1)
    times: list[DayPart] = Field(min_length=1)
    modality: Modality
    #: Defaults to the client's usual session type.
    session_type: str | None = Field(default=None, min_length=1, max_length=MAX_SESSION_TYPE)
    #: 1 is the top. Defaults to the end of the list.
    position: int | None = Field(default=None, ge=1)
    paused: bool = False
    no_auto_offer: bool = False


class UpdateWaitlistEntryRequest(BaseModel):
    """Only the fields sent change."""

    model_config = ConfigDict(extra="forbid")

    days: list[Weekday] | None = Field(default=None, min_length=1)
    times: list[DayPart] | None = Field(default=None, min_length=1)
    modality: Modality | None = None
    session_type: str | None = Field(default=None, min_length=1, max_length=MAX_SESSION_TYPE)
    paused: bool | None = None
    no_auto_offer: bool | None = None


class ReorderWaitlistRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    #: Open entries in the order they should take, first at the top.
    entry_ids: list[str] = Field(min_length=1)


class WaitlistEntryResponse(BaseModel):
    id: str
    patient_id: str
    patient_name: str | None
    days: list[Weekday]
    times: list[DayPart]
    modality: Modality
    session_type: str | None
    position: int
    paused: bool
    no_auto_offer: bool
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_entry(cls, e: WaitlistEntry) -> WaitlistEntryResponse:
        return cls(
            id=e.id,
            patient_id=e.patient_id,
            patient_name=e.patient_name,
            days=list(e.days),  # type: ignore[arg-type]  # the repository stores plain strings; the write path validates them against the Literal
            times=list(e.times),  # type: ignore[arg-type]  # the repository stores plain strings; the write path validates them against the Literal
            modality=e.modality,  # type: ignore[arg-type]  # the repository stores plain strings; the write path validates them against the Literal
            session_type=e.session_type,
            position=e.position,
            paused=e.paused,
            no_auto_offer=e.no_auto_offer,
            created_at=e.created_at,
            updated_at=e.updated_at,
        )


class WaitlistResponse(BaseModel):
    entries: list[WaitlistEntryResponse]


class PatientWaitlistResponse(BaseModel):
    """The client's place on the clinician's list, and what to suggest when adding them."""

    entry: WaitlistEntryResponse | None
    #: The client's usual session type, the default for a new entry.
    usual_session_type: str | None
    #: How the client's past visits were held, read from whether each had a
    #: video link. A suggestion to confirm, not a record.
    past_modalities: list[VisitModality]
