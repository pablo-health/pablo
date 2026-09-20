# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Request and response shapes for signing a consent document.

One request and one response, and the shape of each says something the
route then enforces.

The request carries no patient id, like every other patient route: who is
signing comes off the authenticated principal. ``affirm`` is a separate
field rather than an implication of sending the request, because ticking a
box is the act the screen asks for and a client that forgot to ask for it
should be refused rather than assumed.

The response is the evidence that was recorded, minus the two fields that
describe the request rather than the agreement. The address a request came
from and the browser it came from are stored — they are what a later
question about the circumstances turns on — but they are not handed back to
the person who just signed. Nothing on this surface needs them, and a
screen that printed somebody's IP address would be reading machinery out
loud for no reason.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class SignDocumentRequest(BaseModel):
    """``POST /api/patient/intake/assignments/{id}/signatures``.

    ``typed_name`` is bounded here as well as in the service, so an
    oversized body is refused before anything reads it.
    """

    model_config = ConfigDict(extra="forbid")

    item_id: str
    signer_role: str = Field(max_length=16)
    typed_name: str = Field(max_length=160)
    affirm: bool = False


class IntakeSignatureResponse(BaseModel):
    """What was recorded, as the person who signed it is shown it.

    ``consent_statement`` is the sentence that was agreed under, resolved
    from the version stored on the row rather than from today's constant —
    so a signature taken under older wording reads back as that wording.
    """

    id: str
    assignment_id: str
    item_id: str
    document_version_id: str
    document_digest: str
    signer_role: str
    signer_typed_name: str
    consent_statement_version: str
    consent_statement: str
    signed_at: datetime
    auth_strength: str
    session_id: str | None
    evidence_digest: str


__all__ = ["IntakeSignatureResponse", "SignDocumentRequest"]
