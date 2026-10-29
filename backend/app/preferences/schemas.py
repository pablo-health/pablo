# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Request and response shapes for the patient-preference routes.

Values are checked against their kind by the service, not here: the kind
decides what a value may be, and kinds are registered at runtime.
"""

from __future__ import annotations

from datetime import datetime  # noqa: TC003 — pydantic resolves field types at runtime
from typing import TYPE_CHECKING, Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from .kinds import TEXT_MAX, get_kind

if TYPE_CHECKING:
    from .models import Preference

#: Sources a route caller may name; the others are set by code that migrates or learns.
RouteSource = Literal["clinician", "assistant"]


class AddPreferenceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: str = Field(min_length=1, max_length=64)
    value: dict[str, Any] | None = None
    text: str | None = Field(default=None, max_length=TEXT_MAX)
    strength: str | None = None
    source: RouteSource = "clinician"
    source_ref: str | None = Field(default=None, max_length=128)


class UpdatePreferenceRequest(BaseModel):
    """Only the fields sent change. Sending ``text: null`` clears it."""

    model_config = ConfigDict(extra="forbid")

    value: dict[str, Any] | None = None
    text: str | None = Field(default=None, max_length=TEXT_MAX)
    strength: str | None = None


class PreferenceResponse(BaseModel):
    id: str
    patient_id: str
    domain: str
    kind: str
    kind_label: str
    value: dict[str, Any] | None
    text: str | None
    strength: str | None
    #: Plain words for the value, when the kind supplies them.
    summary: str | None
    source: str
    stated_at: datetime
    confirmed_at: datetime | None
    retired_at: datetime | None
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_preference(cls, p: Preference) -> PreferenceResponse:
        spec = get_kind(p.kind)
        summary = (
            spec.summarize(p.value)
            if spec is not None and spec.summarize is not None and p.value is not None
            else None
        )
        return cls(
            id=p.id,
            patient_id=p.patient_id,
            domain=p.domain,
            kind=p.kind,
            kind_label=spec.label if spec is not None else p.kind,
            value=p.value,
            text=p.text,
            strength=p.strength,
            summary=summary,
            source=p.source,
            stated_at=p.stated_at,
            confirmed_at=p.confirmed_at,
            retired_at=p.retired_at,
            created_at=p.created_at,
            updated_at=p.updated_at,
        )


class PreferenceListResponse(BaseModel):
    data: list[PreferenceResponse]
    total: int
