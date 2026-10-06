# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Move a chat answer to a fallback model when its own fails before answering.

A chat feature's fallbacks come from its entry in ``Settings.ai_fallbacks``.
For each answer the models are tried in order, the feature's own first:

- a model whose stream fails transiently (rate limit, 5xx, timeout, dropped
  connection) before its first word hands over to the next model, and the
  failed attempt shows nothing to the reader;
- once a word has been streamed, that model finishes the answer, failure
  included: an answer is never stitched together from two models;
- a failure that is not transient (a safety block, a refused request) is
  the answer, as it is without fallbacks.

So one answer makes at most one attempt per model, each with its gateway's
own retry of the connection. The turn service's single retry of a failed
answer runs this sequence once more, and that is the most an answer ever
costs: two passes over the configured models.

A fallback is named by model string. ``bedrock:`` models stream through
Bedrock; any other is served by the feature's own gateway, as the primary
is. Each answer that needed a fallback logs one line: the model that
answered and the error code of each that did not. Never prompt or answer
text.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from ..settings import get_settings
from .chat_llm_gateway import ChatLLMGateway, StreamEvent, UserAssistantTurn
from .llm_provider import LLMProvider

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable, Sequence

logger = logging.getLogger(__name__)


def _bedrock_gateway() -> ChatLLMGateway:
    from .bedrock_chat_llm_gateway import get_bedrock_chat_llm_gateway

    return get_bedrock_chat_llm_gateway()


class FailoverChatLLMGateway(ChatLLMGateway):
    """Stream from the first model in order that does not fail before answering."""

    def __init__(
        self,
        gateway: ChatLLMGateway,
        fallbacks: Sequence[str],
        *,
        resolve: Callable[[str], ChatLLMGateway] | None = None,
    ) -> None:
        self._gateway = gateway
        self._fallbacks = tuple(fallbacks)
        self._resolve = resolve or self._default_resolve

    def _default_resolve(self, model: str) -> ChatLLMGateway:
        provider, sep, _ = model.partition(":")
        if sep and provider == LLMProvider.BEDROCK:
            return _bedrock_gateway()
        return self._gateway

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
        models = [model, *(m for m in dict.fromkeys(self._fallbacks) if m != model)]
        failures: list[str] = []
        for index, leg_model in enumerate(models):
            gateway = self._gateway if index == 0 else self._resolve(leg_model)
            stream = gateway.stream_completion(
                model=leg_model,
                system_prompt=system_prompt,
                prior_turns=prior_turns,
                new_user_text=new_user_text,
                max_output_tokens=max_output_tokens,
                temperature=temperature,
            )
            answering = False
            try:
                async for event in stream:
                    if (
                        not answering
                        and not event.delta
                        and event.finish_reason == "error"
                        and event.transient
                        and index < len(models) - 1
                    ):
                        failures.append(f"{leg_model}:{event.error_code}")
                        break
                    if not answering and failures:
                        logger.info(
                            "Chat answered by fallback: model=%s failures=%s",
                            leg_model,
                            ",".join(failures),
                        )
                    answering = True
                    yield event
                else:
                    return
            finally:
                await stream.aclose()  # type: ignore[attr-defined]


def failover_chat_gateway(feature: str, gateway: ChatLLMGateway) -> ChatLLMGateway:
    """``gateway`` with ``feature``'s fallbacks behind it, or unchanged with none."""
    fallbacks = get_settings().fallbacks_for(feature)
    if not fallbacks:
        return gateway
    return FailoverChatLLMGateway(gateway, fallbacks)


__all__ = ["FailoverChatLLMGateway", "failover_chat_gateway"]
