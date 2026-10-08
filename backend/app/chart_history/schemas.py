# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Request / response models for the chart-history API."""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime  # noqa: TC003 — pydantic resolves field types at runtime
from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict, Field

from .fields import HISTORY_GROUPS

if TYPE_CHECKING:
    from .models import HistoryEntry, HistoryRevision


class SetHistoryFieldRequest(BaseModel):
    """Body for ``PUT /api/patients/{patient_id}/chart-history/{key}``.

    A note that a value was accepted from passes its id as ``source_note_id``.
    """

    model_config = ConfigDict(str_strip_whitespace=True)

    text: str = Field(min_length=1, max_length=20_000)
    source_note_id: str | None = None


class HistoryRevisionResponse(BaseModel):
    text: str | None
    written_at: datetime
    written_by: str | None
    replaced_at: datetime
    replaced_by: str
    source_note_id: str | None


class HistoryFieldResponse(BaseModel):
    key: str
    label: str
    text: str | None
    """``None`` when nothing is recorded, or the value was removed."""
    updated_at: datetime | None
    updated_by: str | None
    source_note_id: str | None
    source_note_date: datetime | None
    earlier: list[HistoryRevisionResponse]
    """Values the field held before, most recently replaced first."""


class HistoryGroupResponse(BaseModel):
    key: str
    label: str
    fields: list[HistoryFieldResponse]


class ChartHistoryResponse(BaseModel):
    groups: list[HistoryGroupResponse]


def history_response(
    entries: dict[str, HistoryEntry], revisions: list[HistoryRevision]
) -> ChartHistoryResponse:
    """Every field, recorded or not, in chart order, each with its earlier values."""
    earlier: dict[str, list[HistoryRevisionResponse]] = defaultdict(list)
    for r in revisions:
        earlier[r.field_key].append(
            HistoryRevisionResponse(
                text=r.text,
                written_at=r.written_at,
                written_by=r.written_by,
                replaced_at=r.replaced_at,
                replaced_by=r.replaced_by,
                source_note_id=r.source_note_id,
            )
        )
    return ChartHistoryResponse(
        groups=[
            HistoryGroupResponse(
                key=group.key,
                label=group.label,
                fields=[
                    field_response(f.key, f.label, entries.get(f.key), earlier[f.key])
                    for f in group.fields
                ],
            )
            for group in HISTORY_GROUPS
        ]
    )


def field_response(
    key: str,
    label: str,
    entry: HistoryEntry | None,
    earlier: list[HistoryRevisionResponse],
) -> HistoryFieldResponse:
    return HistoryFieldResponse(
        key=key,
        label=label,
        text=entry.text if entry else None,
        updated_at=entry.updated_at if entry else None,
        updated_by=entry.updated_by if entry else None,
        source_note_id=entry.source_note_id if entry else None,
        source_note_date=entry.source_note_date if entry else None,
        earlier=earlier,
    )
