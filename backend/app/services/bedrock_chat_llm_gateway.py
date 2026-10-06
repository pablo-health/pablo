# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Streaming chat gateway backed by Amazon Bedrock ConverseStream.

The chat sibling of :mod:`.bedrock_structured_llm_gateway`, and the leg a
chat feature falls over to when its own model fails before answering (see
:mod:`.chat_failover`). It takes the same turns the Gemini gateway takes and
yields the same :class:`~.chat_llm_gateway.StreamEvent` items, so the turn
service cannot tell which provider answered.

Bedrock's client is synchronous, so the request and each streamed event are
read on a worker thread; closing the generator early stops reading and
closes the response.

Credentials, region and bounds come from the structured gateway this shares
its clients with. A read timeout bounds the wait for each streamed event,
not the whole answer.

Logging. Only the exception class, the service's error code, the HTTP
status and the model id are logged. Prompts, answers and service error
messages never are.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any

from .bedrock_structured_llm_gateway import (
    BedrockCallError,
    BedrockStructuredLLMGateway,
    _translate_error,
)
from .chat_llm_gateway import (
    ChatLLMGateway,
    FinishReason,
    StreamEvent,
    UserAssistantTurn,
    is_transient_failure,
)
from .llm_provider import LLMProvider, strip_provider_prefix
from .llm_telemetry import LLMSpanRequest, llm_span

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Iterator

logger = logging.getLogger(__name__)

#: How long to wait for the stream to open, and then for each event on it.
_READ_TIMEOUT_SECONDS = 60.0

#: Bedrock requires a conversation to open with a user turn. A windowed
#: history can open with the assistant; this stands in for what came before.
_OPENING_USER_TURN = "(Earlier in this conversation.)"

#: Errors Bedrock reports inside the stream rather than as a failed request.
_STREAM_ERROR_STATUS = {
    "internalServerException": 500,
    "modelStreamErrorException": 500,
    "serviceUnavailableException": 503,
    "throttlingException": 429,
    "validationException": 400,
}

_STOP_REASONS: dict[str, FinishReason] = {
    "end_turn": "stop",
    "stop_sequence": "stop",
    "max_tokens": "length",
    "guardrail_intervened": "safety",
    "content_filtered": "safety",
}


def to_converse_messages(
    prior_turns: list[UserAssistantTurn], new_user_text: str
) -> list[dict[str, Any]]:
    """The turns as Converse messages: user first, roles alternating.

    Empty turns are skipped, as the Gemini gateway skips them, and turns in
    a row from the same side are joined, since Converse rejects two in a row.
    """
    messages: list[dict[str, Any]] = []
    turns = [(t.role, (t.content or "").strip()) for t in prior_turns]
    for role, text in [*turns, ("user", new_user_text)]:
        if not text:
            continue
        if not messages and role != "user":
            messages.append({"role": "user", "content": [{"text": _OPENING_USER_TURN}]})
        if messages and messages[-1]["role"] == role:
            messages[-1]["content"][0]["text"] += f"\n\n{text}"
        else:
            messages.append({"role": role, "content": [{"text": text}]})
    return messages


def _error_code(exc: BaseException) -> str:
    """The same coarse codes the Gemini gateway reports, so the client reads them alike."""
    if isinstance(exc, TimeoutError):
        return "timeout"
    if isinstance(exc, BedrockCallError):
        if exc.status_code in (401, 403):
            return "auth_denied"
        if exc.status_code is not None and exc.status_code >= 500:  # noqa: PLR2004
            return "service_unavailable"
    if isinstance(exc, ConnectionError):
        return "service_unavailable"
    return "llm_error"


def _next_event(events: Iterator[dict[str, Any]]) -> dict[str, Any] | None:
    return next(events, None)


class BedrockChatLLMGateway(ChatLLMGateway):
    """Streams one chat answer through Bedrock ConverseStream."""

    def __init__(self, clients: BedrockStructuredLLMGateway | None = None) -> None:
        self._clients = clients or BedrockStructuredLLMGateway()

    async def stream_completion(
        self,
        *,
        model: str,
        system_prompt: str,
        prior_turns: list[UserAssistantTurn],
        new_user_text: str,
        max_output_tokens: int,
        temperature: float = 0.4,
    ) -> AsyncIterator[StreamEvent]:
        # Never hold a pooled DB connection across the model round-trip.
        from ..db import assert_no_held_db_connection

        assert_no_held_db_connection("chat-llm")
        normalized_model = strip_provider_prefix(model)
        request: dict[str, Any] = {
            "modelId": normalized_model,
            "system": [{"text": system_prompt}],
            "messages": to_converse_messages(prior_turns, new_user_text),
            "inferenceConfig": {"maxTokens": max_output_tokens, "temperature": temperature},
        }
        with llm_span(
            LLMSpanRequest(operation="chat", model=normalized_model, provider=LLMProvider.BEDROCK)
        ) as span:
            stream: Any = None
            output_tokens: int | None = None
            final_reason: FinishReason = "stop"
            try:
                client = self._clients.client(_READ_TIMEOUT_SECONDS)
                try:
                    response = await asyncio.to_thread(lambda: client.converse_stream(**request))
                except Exception as exc:
                    raise _translate_error(exc, _READ_TIMEOUT_SECONDS) from exc
                stream = response["stream"]
                events = iter(stream)
                while True:
                    try:
                        event = await asyncio.to_thread(_next_event, events)
                    except Exception as exc:
                        raise _translate_error(exc, _READ_TIMEOUT_SECONDS) from exc
                    if event is None:
                        break
                    failed = next((k for k in _STREAM_ERROR_STATUS if k in event), None)
                    if failed is not None:
                        raise BedrockCallError(
                            f"Bedrock stream reported {failed}",
                            status_code=_STREAM_ERROR_STATUS[failed],
                            error_code=failed,
                        )
                    delta = (event.get("contentBlockDelta") or {}).get("delta") or {}
                    if delta.get("text"):
                        yield StreamEvent(delta=delta["text"])
                    if "messageStop" in event:
                        reason = event["messageStop"].get("stopReason", "end_turn")
                        final_reason = _STOP_REASONS.get(reason, "stop")
                    usage = (event.get("metadata") or {}).get("usage") or {}
                    if usage.get("outputTokens"):
                        output_tokens = usage["outputTokens"]
            except Exception as exc:
                code = _error_code(exc)
                described = (
                    f"{exc.error_code}, HTTP {exc.status_code}"
                    if isinstance(exc, BedrockCallError)
                    else type(exc).__name__
                )
                logger.warning(
                    "Bedrock chat stream failed: %s (model=%s)", described, normalized_model
                )
                span.set_error_class(code)
                yield StreamEvent(
                    finish_reason="error",
                    error_code=code,
                    error_message=type(exc).__name__,
                    transient=is_transient_failure(exc),
                )
                return
            finally:
                if stream is not None and hasattr(stream, "close"):
                    stream.close()

            if output_tokens:
                span.set_token_usage(completion_tokens=output_tokens)
            yield StreamEvent(finish_reason=final_reason, output_tokens=output_tokens)


_bedrock_chat_holder: list[BedrockChatLLMGateway] = []


def get_bedrock_chat_llm_gateway() -> BedrockChatLLMGateway:
    """The process-wide Bedrock chat gateway, built on first use."""
    if not _bedrock_chat_holder:
        _bedrock_chat_holder.append(BedrockChatLLMGateway())
    return _bedrock_chat_holder[0]


__all__ = ["BedrockChatLLMGateway", "get_bedrock_chat_llm_gateway", "to_converse_messages"]
