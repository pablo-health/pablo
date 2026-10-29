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

A feature may opt in to ``retry_refusals``. Then a model that refuses the
turn (``finish_reason="safety"``) before its first word hands over to the
next model too, and a refusal is the answer only when every model refused.
If a model could not be reached and the last one then refused, the answer
is the failure to reach it, since that model might have answered. A refusal
after the first word is never retried, for the same reason a failure is not.

So one answer makes at most one attempt per model, each with its gateway's
own retry of the connection. The turn service's single retry of a failed
answer runs this sequence once more, and that is the most an answer ever
costs: two passes over the configured models.

Every model, the primary included, is named by model string: ``bedrock:``
models stream through Bedrock, and any other through the feature's own
gateway. So a feature whose primary is a Bedrock model (``ai_models``) can
fall back to the tier default on Vertex, and the other way round.

Each answer that needed a fallback logs one line: the model that
answered and the error code of each that did not. Each answer in which a
model refused logs one line too: every model tried with how it finished,
and whether the answer ended refused, answered by a fallback, or failed.
So the refusal rate can be counted. Never prompt or answer text.
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
        retry_refusals: bool = False,
        feature: str = "",
    ) -> None:
        self._gateway = gateway
        self._fallbacks = tuple(fallbacks)
        self._resolve = resolve or self._default_resolve
        self._retry_refusals = retry_refusals
        self._feature = feature

    def _default_resolve(self, model: str) -> ChatLLMGateway:
        provider, sep, _ = model.partition(":")
        if sep and provider == LLMProvider.BEDROCK:
            return _bedrock_gateway()
        return self._gateway

    def _hands_over(self, event: StreamEvent) -> bool:
        """Whether ``event``, arriving before any word, sends the answer to the next model."""
        if event.delta:
            return False
        if event.finish_reason == "error":
            return event.transient
        return self._retry_refusals and event.finish_reason == "safety"

    def _log_refusal(self, legs: list[str], final: StreamEvent) -> None:
        """One line for an answer in which a model refused: models, reasons, outcome."""
        if final.finish_reason == "safety":
            outcome = "safety"
        elif final.finish_reason in ("stop", "length"):
            outcome = "answered_by_fallback"
        else:
            outcome = "error"
        logger.info(
            "Chat refusal: feature=%s legs=%s final=%s",
            self._feature or "-",
            ",".join(legs),
            outcome,
        )

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
        # Each model that handed over, as ``model:reason``.
        failures: list[str] = []
        refused = False
        unreached: StreamEvent | None = None
        for index, leg_model in enumerate(models):
            gateway = self._resolve(leg_model)
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
                    if not answering and index < len(models) - 1 and self._hands_over(event):
                        if event.finish_reason == "safety":
                            refused = True
                            failures.append(f"{leg_model}:safety")
                        else:
                            unreached = event
                            failures.append(f"{leg_model}:{event.error_code}")
                        break
                    if not answering and failures and not refused:
                        logger.info(
                            "Chat answered by fallback: model=%s failures=%s",
                            leg_model,
                            ",".join(failures),
                        )
                    shown = event
                    if (
                        not answering
                        and self._retry_refusals
                        and event.finish_reason == "safety"
                        and unreached is not None
                    ):
                        # A model that could not be reached might have answered.
                        shown = unreached
                    if event.finish_reason is not None and (
                        refused or event.finish_reason == "safety"
                    ):
                        self._log_refusal([*failures, f"{leg_model}:{event.finish_reason}"], shown)
                    answering = True
                    yield shown
                else:
                    return
            finally:
                await stream.aclose()  # type: ignore[attr-defined]  # AsyncIterator has no aclose; the gateway yields an async generator


def failover_chat_gateway(
    feature: str, gateway: ChatLLMGateway, *, retry_refusals: bool = False
) -> ChatLLMGateway:
    """``gateway`` with ``feature``'s fallbacks behind it, or unchanged with none.

    Also wrapped when the feature has a primary of its own in ``ai_models``,
    so a ``bedrock:`` primary streams through Bedrock with ``gateway``'s
    models as its fallbacks. ``retry_refusals`` lets a refused turn move to
    the next model too (see the module docstring).
    """
    settings = get_settings()
    fallbacks = settings.fallbacks_for(feature)
    if not fallbacks and feature not in settings.ai_models:
        return gateway
    return FailoverChatLLMGateway(
        gateway, fallbacks, retry_refusals=retry_refusals, feature=feature
    )


__all__ = ["FailoverChatLLMGateway", "failover_chat_gateway"]
