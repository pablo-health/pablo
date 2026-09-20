# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Request and response shapes for the practice's consent documents.

Two audiences, two response models, and the difference between them is what
each one is for.

:class:`IntakeDocumentResponse` is the practice's own view while it writes:
the markdown it typed, plus the HTML that markdown renders to so the editor
can show a preview that is the server's answer rather than a second
renderer's guess. :class:`PatientDocumentResponse` is what somebody being
asked to sign gets: the rendered words, the version, and the digest — never
the source, because the source is not what they are agreeing to.

Both carry the digest. It is the same string a signature will record, so
putting it in front of the practice is what lets somebody confirm that the
document on a signed record is the document they think it is.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class CreateDocumentRequest(BaseModel):
    """``POST /api/intake/documents``."""

    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=160)
    body_markdown: str = Field(default="", max_length=100_000)
    requires_signature: bool = True
    signer_roles: list[str] | None = Field(default=None, max_length=4)


class UpdateDocumentRequest(BaseModel):
    """``PUT /api/intake/documents/{id}``.

    Both fields optional, so the editor can save a title change without
    resending the body. An empty body is a no-op that returns the draft
    unchanged.
    """

    model_config = ConfigDict(extra="forbid")

    title: str | None = Field(default=None, min_length=1, max_length=160)
    body_markdown: str | None = Field(default=None, max_length=100_000)


class IntakeDocumentResponse(BaseModel):
    """One version, as the practice that wrote it sees it."""

    id: str
    document_key: str
    title: str
    body_markdown: str
    rendered_html: str
    version: int
    digest: str
    published_at: datetime | None
    requires_signature: bool
    signer_roles: list[str]
    created_at: datetime


class PatientDocumentResponse(BaseModel):
    """One published version, as the person asked to sign it sees it.

    No ``body_markdown``: the rendered words are what was read, and handing
    back the source would give a signing screen two things it could show.

    ``consent_statement`` is the sentence a signature would be taken under —
    served rather than left to the screen to write, because the version of it
    is recorded on the signature. A copy in the front end would be free to
    drift from what a stored signature says was agreed, and nothing would
    look wrong when it did.
    """

    id: str
    document_key: str
    title: str
    rendered_html: str
    version: int
    digest: str
    requires_signature: bool
    signer_roles: list[str]
    consent_statement: str
    consent_statement_version: str


__all__ = [
    "CreateDocumentRequest",
    "IntakeDocumentResponse",
    "PatientDocumentResponse",
    "UpdateDocumentRequest",
]
