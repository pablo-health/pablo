# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Request and response models for the practice admins card."""

from __future__ import annotations

from pydantic import BaseModel


class PracticeClinician(BaseModel):
    """One clinician of the practice, as the owner deciding admins sees them."""

    user_id: str
    name: str | None
    email: str
    is_owner: bool
    is_admin: bool


class PracticeAdminsResponse(BaseModel):
    """Every clinician of the practice, the owner first, then by name."""

    clinicians: list[PracticeClinician]
