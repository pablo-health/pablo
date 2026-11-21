# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Request and response models for practice invitations."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field, field_validator


class CreatePracticeInviteRequest(BaseModel):
    """Who to invite: the address the link is sent to."""

    email: str = Field(min_length=3, max_length=255)

    @field_validator("email")
    @classmethod
    def _looks_like_an_address(cls, value: str) -> str:
        value = value.strip()
        local, _, domain = value.rpartition("@")
        if not local or not domain or "." not in domain or any(c.isspace() for c in value):
            raise ValueError("Enter an email address")
        return value


class PracticeInvite(BaseModel):
    """A pending invitation, as the practice's owner and admins see it. No token."""

    id: str
    email: str
    invited_by: str | None
    created_at: datetime
    expires_at: datetime


class CreatePracticeInviteResponse(PracticeInvite):
    """The invitation just created, and whether its email went out."""

    email_sent: bool


class PracticeInvitesResponse(BaseModel):
    """The practice's pending invitations, newest first."""

    invites: list[PracticeInvite]


class InvitePreviewResponse(BaseModel):
    """What the invitation page shows before anyone signs in."""

    practice_name: str
    email: str


class RedeemInviteRequest(BaseModel):
    """Redeem with this account even though its address differs from the invited one."""

    confirm_mismatch: bool = False


class RedeemInviteResponse(BaseModel):
    """The practice the redeeming account now belongs to."""

    practice_id: str
    practice_name: str
