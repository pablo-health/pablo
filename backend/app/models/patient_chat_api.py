# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Request and response shapes for the patient-principal chat surface.

Separate from :mod:`chat_api` on purpose. The clinician shapes expose the
machinery a clinician is entitled to see — which chart sources fed a turn,
token counts, the model, the feature key. None of that belongs in front of
a patient, and a shared model would leak it by inheritance the first time
a field was added. Each response here names its fields.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from .chat import ChatConversation, ChatMessage  # noqa: TC001 — Pydantic runtime


class CreatePatientChatConversationRequest(BaseModel):
    """``POST /api/patient/chat/conversations`` body. The title is all a patient sets."""

    title: str | None = Field(default=None, max_length=200)


class UpdatePatientChatConversationRequest(BaseModel):
    """``PATCH /api/patient/chat/conversations/{id}`` body."""

    title: str | None = Field(default=None, max_length=200)
    archive: bool | None = None


class SendPatientChatMessageRequest(BaseModel):
    """``POST /api/patient/chat/conversations/{id}/messages`` body.

    No model override and no source selection: the surface never reads the
    chart, and which model answers is the deployment's decision.
    """

    content: str = Field(min_length=1, max_length=32_768)


class PatientChatMessageResponse(BaseModel):
    id: str
    sequence: int
    role: str
    content: str
    created_at: datetime

    @staticmethod
    def from_message(msg: ChatMessage) -> PatientChatMessageResponse:
        return PatientChatMessageResponse(
            id=msg.id,
            sequence=msg.sequence,
            role=msg.role,
            content=msg.content,
            created_at=msg.created_at,
        )


class PatientChatConversationResponse(BaseModel):
    id: str
    title: str
    created_at: datetime
    last_turn_at: datetime | None = None
    archived_at: datetime | None = None

    @staticmethod
    def from_conversation(conv: ChatConversation) -> PatientChatConversationResponse:
        return PatientChatConversationResponse(
            id=conv.id,
            title=conv.title,
            created_at=conv.created_at,
            last_turn_at=conv.last_turn_at,
            archived_at=conv.archived_at,
        )


class PatientChatConversationDetailResponse(PatientChatConversationResponse):
    messages: list[PatientChatMessageResponse] = Field(default_factory=list)

    @staticmethod
    def from_conversation_with_messages(
        conv: ChatConversation, messages: list[ChatMessage]
    ) -> PatientChatConversationDetailResponse:
        return PatientChatConversationDetailResponse(
            id=conv.id,
            title=conv.title,
            created_at=conv.created_at,
            last_turn_at=conv.last_turn_at,
            archived_at=conv.archived_at,
            messages=[PatientChatMessageResponse.from_message(m) for m in messages],
        )


class PatientChatConversationListResponse(BaseModel):
    data: list[PatientChatConversationResponse]
    total: int


__all__ = [
    "CreatePatientChatConversationRequest",
    "PatientChatConversationDetailResponse",
    "PatientChatConversationListResponse",
    "PatientChatConversationResponse",
    "PatientChatMessageResponse",
    "SendPatientChatMessageRequest",
    "UpdatePatientChatConversationRequest",
]
