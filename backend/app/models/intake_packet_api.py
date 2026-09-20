# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Request and response shapes for the intake form builder.

This is the clinician's side of intake — building the form, not answering
it. Nothing here carries a patient id, an answer or a submission, because a
template belongs to the practice rather than to anybody on it.

``config`` stays an open mapping on the way in and on the way out. What a
valid configuration looks like depends on the item's type, and
:mod:`app.intake.items` is where that is decided; duplicating it into a
second set of per-type request models would give the editor two schemas to
satisfy and this file a reason to drift from the one that is enforced.

A draft may be incomplete. The editor saves as a practice works, so a
half-filled item is a normal thing to store and only publishing insists the
whole form makes sense.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from ..intake.items import HELP_TEXT_MAX_LEN, LABEL_MAX_LEN


class IntakeItemRequest(BaseModel):
    """One item as the editor sends it.

    ``label`` is the question the patient will read and ``help_text`` the
    line under it. Both are optional here and neither is checked for
    emptiness: a practice writing a form saves it half-written, and which
    types have to have a question by the time it is published is
    :mod:`app.intake.items`'s to say.
    """

    model_config = ConfigDict(extra="forbid")

    key: str = Field(min_length=1, max_length=64)
    item_type: str = Field(min_length=1, max_length=32)
    required: bool = True
    resign_on_new_version: bool = False
    label: str | None = Field(default=None, max_length=LABEL_MAX_LEN)
    help_text: str | None = Field(default=None, max_length=HELP_TEXT_MAX_LEN)
    config: dict[str, object] = Field(default_factory=dict)


class ReplaceItemsRequest(BaseModel):
    """``PUT /api/intake/templates/{id}/versions/{vid}/items``.

    The whole ordered list, every time. Position is the list index, so there
    is no position field to get out of step with the order.
    """

    model_config = ConfigDict(extra="forbid")

    items: list[IntakeItemRequest] = Field(max_length=200)


class CreateTemplateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=120)


class UpdateTemplateRequest(BaseModel):
    """Rename a template, archive it, or bring it back.

    Both fields are optional and an empty body is a no-op that returns the
    template unchanged.
    """

    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=120)
    archived: bool | None = None


class IntakeItemResponse(BaseModel):
    """One stored item."""

    id: str
    key: str
    position: int
    item_type: str
    required: bool
    resign_on_new_version: bool
    label: str | None
    help_text: str | None
    config: dict[str, object]


class IntakeVersionResponse(BaseModel):
    """One version, without its items.

    ``published_at`` is the whole state: set means frozen, unset means the
    practice is still editing it.
    """

    id: str
    version: int
    published_at: datetime | None
    created_at: datetime


class IntakeVersionDetailResponse(IntakeVersionResponse):
    """One version and the items on it, in order."""

    template_id: str
    items: list[IntakeItemResponse]


class IntakeTemplateResponse(BaseModel):
    """One template and its versions, newest number first."""

    id: str
    name: str
    created_at: datetime
    archived_at: datetime | None
    versions: list[IntakeVersionResponse]


__all__ = [
    "CreateTemplateRequest",
    "IntakeItemRequest",
    "IntakeItemResponse",
    "IntakeTemplateResponse",
    "IntakeVersionDetailResponse",
    "IntakeVersionResponse",
    "ReplaceItemsRequest",
    "UpdateTemplateRequest",
]
