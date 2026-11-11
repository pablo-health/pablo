# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Streaming chat gateway backed by Claude on Vertex AI.

The chat sibling of :class:`~.structured_llm_gateway.AnthropicStructuredLLMGateway`
and of :mod:`.bedrock_chat_llm_gateway`. It takes the same turns the other chat
gateways take and yields the same :class:`~.chat_llm_gateway.StreamEvent` items,
so the turn service cannot tell which provider answered. Selected for
``anthropic:``-prefixed model ids by :mod:`.chat_failover`.

The project comes from ``ANTHROPIC_VERTEX_PROJECT_ID``, else
``GOOGLE_CLOUD_PROJECT`` (see :mod:`.vertex_client`). Parameters a model
rejects are left out of the request (see :mod:`.claude_models`). A read
timeout bounds the wait for each streamed chunk, not the whole answer.

An error the server sends after the stream has opened is read by its error
type (see ``_STREAM_ERROR_STATUS``), so an overloaded model hands over to the
fallback like one that failed to open.

Logging. Only the exception class, the HTTP status and the model id are
logged. Prompts, answers and service error messages never are.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any

from .chat_llm_gateway import (
    PATIENT_CONTEXT_DELIMITER,
    ChatLLMGateway,
    FinishReason,
    StreamEvent,
    UserAssistantTurn,
    alternating_turns,
    is_transient_failure,
)
from .claude_models import effort_kwargs, sampling_kwargs
from .llm_provider import LLMProvider, strip_provider_prefix
from .llm_telemetry import LLMSpanRecorder, LLMSpanRequest, llm_span
from .vertex_client import async_anthropic_vertex_client

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

logger = logging.getLogger(__name__)

#: How long to wait for the connection, and then for each chunk on it.
_READ_TIMEOUT_SECONDS = 60.0

#: How an Anthropic ``stop_reason`` ends the answer. When Claude declines a
#: turn itself it usually says so in ordinary text with ``end_turn``: that is
#: an answer, and nothing here reads its text. Only an explicit ``refusal``
#: ends ``safety``, as does an answer that stopped without a word (see
#: ``_finish``).
_STOP_REASONS: dict[str, FinishReason] = {
    "end_turn": "stop",
    "stop_sequence": "stop",
    "max_tokens": "length",
    "refusal": "safety",
}


def _finish(stop_reason: str | None, *, said_anything: bool) -> FinishReason:
    """The finish for ``stop_reason``: an answer that ended with no text is a refusal.

    A model that stops without a word has declined to answer; reading that as
    an empty ``stop`` would show the reader nothing and say nothing went wrong.
    """
    reason = _STOP_REASONS.get(stop_reason or "", "stop")
    if reason == "stop" and not said_anything:
        return "safety"
    return reason


def system_blocks(system_prompt: str) -> list[dict[str, Any]]:
    """The system prompt as text blocks, caching only what is stable.

    The chat service appends the patient's chart to the system prompt after
    :data:`PATIENT_CONTEXT_DELIMITER`. Only the part before it is the same
    across conversations, so only that part carries the cache checkpoint; the
    chart follows it, sent on every call and written to no cache. The model
    reads the blocks as one system prompt.
    """
    stable, delimiter, patient_context = system_prompt.partition(PATIENT_CONTEXT_DELIMITER)
    blocks: list[dict[str, Any]] = []
    if stable.strip():
        blocks.append({"type": "text", "text": stable, "cache_control": {"type": "ephemeral"}})
    if delimiter + patient_context:
        blocks.append({"type": "text", "text": delimiter + patient_context})
    return blocks


def to_anthropic_messages(
    prior_turns: list[UserAssistantTurn], new_user_text: str
) -> list[dict[str, Any]]:
    """The turns as Anthropic messages: user first, roles alternating."""
    return [
        {"role": role, "content": text}
        for role, text in alternating_turns(prior_turns, new_user_text)
    ]


#: The HTTP status an error sent inside an open stream stands for, by its
#: Anthropic error type. Once a stream has opened its response is a 200, and
#: the SDK raises ``event: error`` with that 200, which reads as neither a
#: failure of the service nor one worth another attempt. The Bedrock gateway's
#: ``_STREAM_ERROR_STATUS`` does the same for ConverseStream. 529 is
#: Anthropic's status for an overloaded service. A type not listed keeps the
#: response's status.
_STREAM_ERROR_STATUS: dict[str, int] = {
    "overloaded_error": 529,
    "api_error": 500,
    "rate_limit_error": 429,
    "timeout_error": 504,
}

#: Statuses worth asking again, here or on the next model: the shared LLM
#: retry statuses plus Anthropic's 529. Kept local so that only this gateway
#: reads 529 as transient; the shared retry policy is unchanged.
_TRANSIENT_STATUS = frozenset({429, 500, 502, 503, 504, 529})


def _status(exc: BaseException) -> int | None:
    """The HTTP status ``exc`` stands for: its own, or its in-stream error type's."""
    import anthropic  # only reached once a call has already failed

    status = getattr(exc, "status_code", None)
    if not isinstance(status, int):
        return None
    if isinstance(exc, anthropic.APIStatusError) and status < 400:  # noqa: PLR2004
        # The stream opened, then the server sent ``event: error``.
        body = exc.body
        error = body.get("error") if isinstance(body, dict) else None
        error_type = error.get("type") if isinstance(error, dict) else None
        if isinstance(error_type, str) and error_type in _STREAM_ERROR_STATUS:
            return _STREAM_ERROR_STATUS[error_type]
    return status


def _is_transient(exc: BaseException) -> bool:
    return is_transient_failure(exc) or _status(exc) in _TRANSIENT_STATUS


def _error_code(exc: BaseException) -> str:
    """The same coarse codes the other chat gateways report, so the client reads them alike."""
    import anthropic  # only reached once a call has already failed

    if isinstance(exc, (TimeoutError, anthropic.APITimeoutError)):
        return "timeout"
    status = _status(exc)
    if status in (401, 403):
        return "auth_denied"
    if status is not None and status >= 500:  # noqa: PLR2004
        return "service_unavailable"
    if isinstance(exc, (ConnectionError, anthropic.APIConnectionError)):
        return "service_unavailable"
    return "llm_error"


class AnthropicVertexChatLLMGateway(ChatLLMGateway):
    """Streams one chat answer from Claude on Vertex AI."""

    def __init__(self, client: Any = None, *, effort: str | None = None) -> None:
        # The reasoning effort every answer from this gateway asks for, when the
        # model takes one (see ``claude_models.effort_kwargs``).
        self._effort = effort
        # A client passed in is used as given (tests). Otherwise one is built on
        # first use for the running event loop: an async client keeps its
        # connections on the loop that opened them, and a job that runs
        # ``asyncio.run`` more than once would otherwise reuse a closed one.
        self._given_client = client
        self._client: Any = None
        self._client_loop: asyncio.AbstractEventLoop | None = None

    def _get_client(self) -> Any:
        if self._given_client is not None:
            return self._given_client
        loop = asyncio.get_running_loop()
        if self._client is None or self._client_loop is not loop:
            self._client = async_anthropic_vertex_client(timeout_seconds=_READ_TIMEOUT_SECONDS)
            self._client_loop = loop
        return self._client

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
        # Never hold a pooled DB connection across the model round-trip:
        # release it here, at the one seam every call passes through.
        from ..db import release_db_connection_before_external_call

        release_db_connection_before_external_call("chat-llm")
        normalized_model = strip_provider_prefix(model)
        request: dict[str, Any] = {
            "model": normalized_model,
            "max_tokens": max_output_tokens,
            "messages": to_anthropic_messages(prior_turns, new_user_text),
            **sampling_kwargs(normalized_model, temperature),
            **effort_kwargs(normalized_model, self._effort),
        }
        if system := system_blocks(system_prompt):
            request["system"] = system
        with llm_span(
            LLMSpanRequest(operation="chat", model=normalized_model, provider=LLMProvider.ANTHROPIC)
        ) as span:
            said_anything = False
            try:
                async with self._get_client().messages.stream(**request) as stream:
                    async for text in stream.text_stream:
                        if text:
                            said_anything = True
                            yield StreamEvent(delta=text)
                    final = await stream.get_final_message()
            except Exception as exc:
                code = _error_code(exc)
                status = _status(exc)
                logger.warning(
                    "Claude on Vertex chat stream failed: %s%s (model=%s)",
                    type(exc).__name__,
                    f", HTTP {status}" if status is not None else "",
                    normalized_model,
                )
                span.set_error_class(code)
                yield StreamEvent(
                    finish_reason="error",
                    error_code=code,
                    error_message=type(exc).__name__,
                    transient=_is_transient(exc),
                )
                return

            usage = getattr(final, "usage", None)
            output_tokens = getattr(usage, "output_tokens", None) or None
            _record_usage(span, usage, output_tokens)
            yield StreamEvent(
                finish_reason=_finish(
                    getattr(final, "stop_reason", None), said_anything=said_anything
                ),
                output_tokens=output_tokens,
            )


def _record_usage(span: LLMSpanRecorder, usage: Any, output_tokens: int | None) -> None:
    """Put a stream's token counts on the span.

    Anthropic reports cache reads and cache writes apart from ``input_tokens``;
    both are folded into the prompt so it means the whole prompt on every
    provider. Only the reads were served from cache.
    """
    input_tokens = getattr(usage, "input_tokens", None)
    cached_tokens = getattr(usage, "cache_read_input_tokens", None)
    written_tokens = getattr(usage, "cache_creation_input_tokens", None)
    if not output_tokens and input_tokens is None:
        return
    span.set_token_usage(
        prompt_tokens=(
            None
            if input_tokens is None
            else input_tokens + (cached_tokens or 0) + (written_tokens or 0)
        ),
        completion_tokens=output_tokens,
        cached_tokens=cached_tokens or None,
    )


#: One gateway per effort, built on first use, so a feature asking for ``low`` and
#: one asking for nothing do not share a setting.
_anthropic_vertex_chat_gateways: dict[str | None, AnthropicVertexChatLLMGateway] = {}


def get_anthropic_vertex_chat_llm_gateway(
    effort: str | None = None,
) -> AnthropicVertexChatLLMGateway:
    """The process-wide Claude-on-Vertex chat gateway for ``effort``."""
    if effort not in _anthropic_vertex_chat_gateways:
        _anthropic_vertex_chat_gateways[effort] = AnthropicVertexChatLLMGateway(effort=effort)
    return _anthropic_vertex_chat_gateways[effort]


__all__ = [
    "AnthropicVertexChatLLMGateway",
    "get_anthropic_vertex_chat_llm_gateway",
    "system_blocks",
    "to_anthropic_messages",
]
