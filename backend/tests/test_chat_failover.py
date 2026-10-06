# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Chat answers move to a fallback model only before their first word.

``chat_failover`` decides; ``bedrock_chat_llm_gateway`` is the leg it most
often falls over to. Async bodies run under ``asyncio.run``, as in the turn
service tests (no pytest-asyncio in this suite).
"""

from __future__ import annotations

import asyncio
import dataclasses
import json
import logging
from typing import TYPE_CHECKING, Any

import pytest
from app.reliability import RetryExhaustedError
from app.repositories import InMemoryChatRepository, InMemoryNotesRepository
from app.services.ai_features import AIFeature
from app.services.bedrock_chat_llm_gateway import BedrockChatLLMGateway, to_converse_messages
from app.services.bedrock_structured_llm_gateway import BedrockStructuredLLMGateway
from app.services.chat_failover import FailoverChatLLMGateway, failover_chat_gateway
from app.services.chat_llm_gateway import (
    ChatLLMGateway,
    FakeChatLLMGateway,
    StreamEvent,
    UserAssistantTurn,
    is_transient_failure,
)
from app.services.chat_turn_service import ChatTurnService
from app.settings import get_settings
from botocore.exceptions import ClientError, ReadTimeoutError

from .test_chat_turn_service import _drain, _make_context, _make_conversation, _no_sleep

if TYPE_CHECKING:
    from collections.abc import Iterator

FALLBACK = "test:fallback"
SAID = "Jane Roe asked about the lighthouse"

_DOWN = StreamEvent(finish_reason="error", error_code="service_unavailable", transient=True)
_REFUSED = StreamEvent(finish_reason="error", error_code="auth_denied", transient=False)
_ANSWER = [
    StreamEvent(delta="From the "),
    StreamEvent(delta="fallback."),
    StreamEvent(finish_reason="stop"),
]


def _stream(gateway: ChatLLMGateway, model: str = "primary") -> list[StreamEvent]:
    async def run() -> list[StreamEvent]:
        return [
            e
            async for e in gateway.stream_completion(
                model=model,
                system_prompt="You help.",
                prior_turns=[],
                new_user_text=SAID,
                max_output_tokens=64,
            )
        ]

    return asyncio.run(run())


@pytest.fixture
def mapped(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """A fallback for the clinician's chat only."""
    monkeypatch.setenv("AI_FALLBACKS", json.dumps({"chat": FALLBACK}))
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


class TestFailover:
    def test_with_nothing_named_the_gateway_is_unchanged(self) -> None:
        fake = FakeChatLLMGateway()
        assert failover_chat_gateway(AIFeature.CHAT, fake) is fake

    @pytest.mark.usefixtures("mapped")
    def test_only_the_named_feature_fails_over(self) -> None:
        fake = FakeChatLLMGateway()
        assert isinstance(failover_chat_gateway(AIFeature.CHAT, fake), FailoverChatLLMGateway)
        assert failover_chat_gateway(AIFeature.PATIENT_CHAT, fake) is fake

    def test_a_transient_failure_before_any_word_moves_to_the_fallback(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        fake = FakeChatLLMGateway(scripts=[[_DOWN], list(_ANSWER)])
        with caplog.at_level(logging.DEBUG):
            events = _stream(FailoverChatLLMGateway(fake, [FALLBACK]))

        assert events == _ANSWER, "the failed attempt shows nothing"
        assert [c["model"] for c in fake.calls] == ["primary", FALLBACK]
        assert "model=test:fallback failures=primary:service_unavailable" in caplog.text
        assert "lighthouse" not in caplog.text

    def test_a_failure_after_the_first_word_is_never_stitched_to_another_model(self) -> None:
        cut = [StreamEvent(delta="Half an "), _DOWN]
        fake = FakeChatLLMGateway(scripts=[list(cut), list(_ANSWER)])

        assert _stream(FailoverChatLLMGateway(fake, [FALLBACK])) == cut
        assert len(fake.calls) == 1

    def test_a_refusal_is_the_answer_not_a_reason_to_ask_elsewhere(self) -> None:
        fake = FakeChatLLMGateway(scripts=[[_REFUSED], list(_ANSWER)])

        assert _stream(FailoverChatLLMGateway(fake, [FALLBACK])) == [_REFUSED]
        assert len(fake.calls) == 1

    def test_a_safety_block_is_the_answer(self) -> None:
        blocked = StreamEvent(finish_reason="safety")
        fake = FakeChatLLMGateway(scripts=[[blocked], list(_ANSWER)])

        assert _stream(FailoverChatLLMGateway(fake, [FALLBACK])) == [blocked]

    def test_when_every_model_fails_the_last_failure_is_reported(self) -> None:
        last = StreamEvent(finish_reason="error", error_code="timeout", transient=True)
        fake = FakeChatLLMGateway(scripts=[[_DOWN], [last]])

        assert _stream(FailoverChatLLMGateway(fake, [FALLBACK])) == [last]

    def test_a_bedrock_fallback_streams_through_bedrock(self) -> None:
        primary = FakeChatLLMGateway(scripts=[[_DOWN]])
        bedrock = FakeChatLLMGateway(scripts=[list(_ANSWER)])
        gateway = FailoverChatLLMGateway(
            primary,
            ["bedrock:some-model"],
            resolve=lambda m: bedrock if m.startswith("bedrock:") else primary,
        )
        assert _stream(gateway) == _ANSWER
        assert [c["model"] for c in bedrock.calls] == ["bedrock:some-model"]


class TestTurnService:
    def _service(self, fake: FakeChatLLMGateway) -> ChatTurnService:
        repo = InMemoryChatRepository()
        repo.grant_all_access()
        repo.add_conversation(_make_conversation(), "user-turn-1")
        notes = InMemoryNotesRepository()
        notes.grant_all_access()
        return ChatTurnService(chat_repo=repo, notes_repo=notes, gateway=fake)

    @pytest.mark.usefixtures("mapped")
    def test_a_clinicians_answer_comes_from_the_fallback_without_a_retry(self) -> None:
        fake = FakeChatLLMGateway(scripts=[[_DOWN], list(_ANSWER)])
        ChatTurnService._conversation_locks.clear()

        events = _drain(self._service(fake), _make_context())

        text = "".join(str(e.data["text"]) for e in events if e.kind == "delta")
        assert text == "From the fallback."
        assert events[-1].kind == "done"
        assert [c["model"] for c in fake.calls] == ["gemini-test-flash", FALLBACK]

    def test_a_bedrock_primary_streams_through_bedrock_and_falls_back_to_the_default(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A primary swap: the feature's own model is on Bedrock, the tier default behind it."""
        monkeypatch.setenv("AI_MODELS", json.dumps({"chat": "bedrock:claude"}))
        monkeypatch.setenv("AI_FALLBACKS", json.dumps({"chat": "gemini-test-flash"}))
        get_settings.cache_clear()
        bedrock = FakeChatLLMGateway(scripts=[[_DOWN]])
        monkeypatch.setattr("app.services.chat_failover._bedrock_gateway", lambda: bedrock)
        vertex = FakeChatLLMGateway(scripts=[list(_ANSWER)])
        ChatTurnService._conversation_locks.clear()
        try:
            events = _drain(self._service(vertex), _make_context())
        finally:
            get_settings.cache_clear()

        assert [c["model"] for c in bedrock.calls] == ["bedrock:claude"]
        assert [c["model"] for c in vertex.calls] == ["gemini-test-flash"]
        assert "".join(str(e.data["text"]) for e in events if e.kind == "delta") == (
            "From the fallback."
        )

    @pytest.mark.usefixtures("mapped")
    def test_a_clients_answer_never_inherits_the_clinician_chats_fallback(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("app.services.chat_turn_service._retry_sleep", _no_sleep)
        fake = FakeChatLLMGateway(scripts=[[_DOWN], list(_ANSWER)])
        ChatTurnService._conversation_locks.clear()
        context = dataclasses.replace(
            _make_context(), patient_principal=True, ground_in_chart=False
        )

        _drain(self._service(fake), context)

        assert FALLBACK not in [c["model"] for c in fake.calls]


class _StubBedrock:
    """A bedrock-runtime client whose converse_stream replays fixed events."""

    def __init__(self, outcome: list[dict[str, Any]] | Exception) -> None:
        self._outcome = outcome
        self.requests: list[dict[str, Any]] = []

    def converse_stream(self, **request: Any) -> dict[str, Any]:
        self.requests.append(request)
        if isinstance(self._outcome, Exception):
            raise self._outcome
        return {"stream": iter(self._outcome)}


def _bedrock(
    outcome: list[dict[str, Any]] | Exception,
) -> tuple[BedrockChatLLMGateway, _StubBedrock]:
    client = _StubBedrock(outcome)
    clients = BedrockStructuredLLMGateway(client_factory=lambda _timeout: client)
    return BedrockChatLLMGateway(clients), client


def _no_db_guard(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.db.assert_no_held_db_connection", lambda _label: None)


class TestBedrockChat:
    def test_text_deltas_stream_and_the_stop_reason_maps(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _no_db_guard(monkeypatch)
        gateway, client = _bedrock(
            [
                {"messageStart": {"role": "assistant"}},
                {"contentBlockDelta": {"delta": {"text": "Sleep "}}},
                {"contentBlockDelta": {"delta": {"text": "improved."}}},
                {"messageStop": {"stopReason": "max_tokens"}},
                {"metadata": {"usage": {"outputTokens": 7}}},
            ]
        )

        events = _stream(gateway, "bedrock:us.some-model")

        assert [e.delta for e in events if e.delta] == ["Sleep ", "improved."]
        assert events[-1] == StreamEvent(finish_reason="length", output_tokens=7)
        assert client.requests[0]["modelId"] == "us.some-model"
        assert client.requests[0]["system"] == [{"text": "You help."}]

    def test_a_throttled_request_is_a_transient_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _no_db_guard(monkeypatch)
        throttled = ClientError(
            {
                "Error": {"Code": "ThrottlingException", "Message": "SECRET-marker"},
                "ResponseMetadata": {"HTTPStatusCode": 429},
            },
            "ConverseStream",
        )
        gateway, _ = _bedrock(throttled)

        (event,) = _stream(gateway, "bedrock:m")

        assert (event.finish_reason, event.transient) == ("error", True)
        assert "SECRET" not in (event.error_message or "")

    def test_a_timeout_is_a_transient_timeout(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _no_db_guard(monkeypatch)
        gateway, _ = _bedrock(ReadTimeoutError(endpoint_url="https://bedrock.invalid"))

        (event,) = _stream(gateway, "bedrock:m")

        assert (event.error_code, event.transient) == ("timeout", True)

    def test_access_denied_is_not_transient(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _no_db_guard(monkeypatch)
        denied = ClientError(
            {
                "Error": {"Code": "AccessDeniedException"},
                "ResponseMetadata": {"HTTPStatusCode": 403},
            },
            "ConverseStream",
        )
        gateway, _ = _bedrock(denied)

        (event,) = _stream(gateway, "bedrock:m")

        assert (event.error_code, event.transient) == ("auth_denied", False)

    def test_an_error_inside_the_stream_ends_it_after_what_was_said(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _no_db_guard(monkeypatch)
        gateway, _ = _bedrock(
            [
                {"contentBlockDelta": {"delta": {"text": "Partly "}}},
                {"modelStreamErrorException": {"message": "SECRET-marker"}},
            ]
        )

        events = _stream(gateway, "bedrock:m")

        assert events[0].delta == "Partly "
        assert (events[-1].finish_reason, events[-1].transient) == ("error", True)


class TestConverseMessages:
    def test_turns_alternate_and_open_with_the_user(self) -> None:
        turns = [
            UserAssistantTurn(role="assistant", content="[earlier turns omitted]"),
            UserAssistantTurn(role="user", content="First"),
            UserAssistantTurn(role="user", content="Second"),
            UserAssistantTurn(role="assistant", content=""),
            UserAssistantTurn(role="assistant", content="Reply"),
        ]
        messages = to_converse_messages(turns, "Now")

        assert [m["role"] for m in messages] == ["user", "assistant", "user", "assistant", "user"]
        assert messages[2]["content"][0]["text"] == "First\n\nSecond"
        assert messages[-1]["content"][0]["text"] == "Now"


def test_a_retry_given_up_on_counts_as_transient() -> None:
    exhausted = RetryExhaustedError(attempts=2, last_exc=TimeoutError())
    wrapped = RuntimeError("stream failed")
    wrapped.__cause__ = exhausted
    assert is_transient_failure(wrapped)
    assert not is_transient_failure(PermissionError("403"))
