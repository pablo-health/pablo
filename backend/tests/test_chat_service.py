# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Tests for the chat conversation service."""

from datetime import UTC, datetime

import pytest
from app.models.chat import ChatMessage
from app.models.patient import Patient
from app.repositories import (
    InMemoryChatRepository,
    InMemoryNotesRepository,
    InMemoryPatientRepository,
    InMemoryTherapySessionRepository,
)
from app.services.chat_service import (
    ChatPatientNotFoundError,
    ChatPermissionError,
    ChatService,
    build_prompt_envelope,
    manifest_digest,
)


def _make_patient(user_id: str = "user-1") -> Patient:
    return Patient(
        id="pat-1",
        user_id=user_id,
        first_name="Sam",
        last_name="Lee",
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
        updated_at=datetime(2026, 1, 1, tzinfo=UTC),
    )


@pytest.fixture
def chat_service() -> ChatService:
    sessions = InMemoryTherapySessionRepository()
    patients = InMemoryPatientRepository(session_repo=sessions)
    patients.create(_make_patient())
    return ChatService(
        chat_repo=InMemoryChatRepository(),
        notes_repo=InMemoryNotesRepository(),
        patient_repo=patients,
    )


def test_create_conversation_uses_default_title_when_omitted(
    chat_service: ChatService,
) -> None:
    conv = chat_service.create_conversation(
        owner_user_id="user-1",
        patient_id="pat-1",
        caller_feature_key="chart_qa",
        caller_system_prompt="You are an assistant.",
        title=None,
        default_source_selection=None,
    )
    assert conv.title.startswith("Chat about Sam Lee")
    assert conv.caller_feature_key == "chart_qa"
    assert conv.default_source_selection  # default applied


def test_create_conversation_rejects_missing_patient(
    chat_service: ChatService,
) -> None:
    with pytest.raises(ChatPatientNotFoundError):
        chat_service.create_conversation(
            owner_user_id="user-1",
            patient_id="missing",
            caller_feature_key="chart_qa",
            caller_system_prompt="prompt",
            title=None,
            default_source_selection=None,
        )


def test_get_conversation_blocks_other_user(
    chat_service: ChatService,
) -> None:
    conv = chat_service.create_conversation(
        owner_user_id="user-1",
        patient_id="pat-1",
        caller_feature_key="chart_qa",
        caller_system_prompt="prompt",
        title="t",
        default_source_selection=None,
    )
    with pytest.raises(ChatPermissionError):
        chat_service.get_conversation(conv.id, requesting_user_id="user-2")


def test_begin_turn_persists_user_and_placeholder_assistant(
    chat_service: ChatService,
) -> None:
    conv = chat_service.create_conversation(
        owner_user_id="user-1",
        patient_id="pat-1",
        caller_feature_key="chart_qa",
        caller_system_prompt="prompt",
        title="t",
        default_source_selection=None,
    )
    _, user_msg, assistant_msg, bundle = chat_service.begin_turn(
        conversation_id=conv.id,
        requesting_user_id="user-1",
        content="Hello",
        source_selection=None,
    )
    assert user_msg.role == "user"
    assert user_msg.content == "Hello"
    assert assistant_msg.role == "assistant"
    assert assistant_msg.content == ""
    assert bundle.patient_id == "pat-1"

    msgs = chat_service.list_messages(conv.id)
    assert [m.role for m in msgs] == ["user", "assistant"]
    assert [m.sequence for m in msgs] == [1, 2]


def test_finalize_turn_records_tokens_and_advances_last_turn_at(
    chat_service: ChatService,
) -> None:
    conv = chat_service.create_conversation(
        owner_user_id="user-1",
        patient_id="pat-1",
        caller_feature_key="chart_qa",
        caller_system_prompt="prompt",
        title="t",
        default_source_selection=None,
    )
    _, _, assistant_msg, _ = chat_service.begin_turn(
        conversation_id=conv.id,
        requesting_user_id="user-1",
        content="Hello",
        source_selection=None,
    )
    finalized = chat_service.finalize_turn(
        assistant_msg.id,
        content="Hi back",
        input_tokens=10,
        output_tokens=3,
        llm_model="gemini-flash",
        llm_finish_reason="stop",
        llm_error=None,
    )
    assert finalized is not None
    assert finalized.content == "Hi back"
    assert finalized.output_tokens == 3
    refreshed = chat_service.get_conversation(conv.id, requesting_user_id="user-1")
    assert refreshed.last_turn_at is not None


def test_purge_conversation_returns_message_count(chat_service: ChatService) -> None:
    conv = chat_service.create_conversation(
        owner_user_id="user-1",
        patient_id="pat-1",
        caller_feature_key="chart_qa",
        caller_system_prompt="prompt",
        title="t",
        default_source_selection=None,
    )
    chat_service.begin_turn(
        conversation_id=conv.id,
        requesting_user_id="user-1",
        content="hi",
        source_selection=None,
    )
    _, count = chat_service.purge_conversation(conv.id, requesting_user_id="user-1")
    assert count == 2  # user + placeholder assistant


def test_build_prompt_envelope_has_canonical_section_order() -> None:
    prior = [
        ChatMessage(
            id="u1",
            conversation_id="c",
            sequence=1,
            role="user",
            content="prior question",
            created_at=datetime(2026, 1, 1, tzinfo=UTC),
        ),
        ChatMessage(
            id="a1",
            conversation_id="c",
            sequence=2,
            role="assistant",
            content="prior answer",
            created_at=datetime(2026, 1, 1, tzinfo=UTC),
        ),
    ]
    envelope = build_prompt_envelope(
        caller_system_prompt="SYS",
        bundle_text="CTX",
        prior_messages=prior,
        new_user_message="ask",
    )
    sys_idx = envelope.index("SYS")
    ctx_idx = envelope.index("--- PATIENT CONTEXT ---")
    conv_idx = envelope.index("--- CONVERSATION ---")
    new_idx = envelope.rindex("User: ask")
    assistant_marker = envelope.rindex("Assistant:")
    assert sys_idx < ctx_idx < conv_idx < new_idx < assistant_marker


def test_manifest_digest_is_stable() -> None:
    manifest = {"a": 1, "b": [1, 2, 3]}
    again = {"b": [1, 2, 3], "a": 1}
    assert manifest_digest(manifest) == manifest_digest(again)
    assert manifest_digest(manifest).startswith("sha256:")
