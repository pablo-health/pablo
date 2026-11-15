# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""A chat gateway answered by an HTTP service rather than a model.

The chat counterpart of :mod:`.http_structured_llm_gateway`, for the practices
routed to a stand-in (:mod:`.model_standin`). Each answer is one
``POST {base_url}/v1/chat`` carrying what the model would have been sent; the
reply's ``text`` comes back as a single delta and a ``stop``, so the turn
service, its usage metering and the reader's stream all run as they do against
a model. A failure comes back the way a model's does, as an ``error`` event
with a coarse code, transient when another attempt could succeed.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import httpx

from .chat_llm_gateway import ChatLLMGateway, StreamEvent, UserAssistantTurn

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

_TIMEOUT_SECONDS = 10.0


class HttpChatLLMGateway(ChatLLMGateway):
    """POST each answer to ``{base_url}/v1/chat`` and stream back its ``text``."""

    def __init__(self, base_url: str) -> None:
        self._url = base_url.rstrip("/") + "/v1/chat"

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
        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT_SECONDS) as client:
                response = await client.post(
                    self._url,
                    json={
                        "model": model,
                        "system_prompt": system_prompt,
                        "prior_turns": [
                            {"role": turn.role, "content": turn.content} for turn in prior_turns
                        ],
                        "new_user_text": new_user_text,
                        "max_output_tokens": max_output_tokens,
                        "temperature": temperature,
                    },
                )
        except httpx.TimeoutException as exc:
            yield _error("timeout", exc, transient=True)
            return
        except httpx.HTTPError as exc:
            yield _error("llm_error", exc, transient=True)
            return
        if response.status_code >= httpx.codes.INTERNAL_SERVER_ERROR:
            yield _error("service_unavailable", None, transient=True)
            return
        if response.is_error:
            yield _error("llm_error", None, transient=False)
            return
        body = response.json()
        text = str(body.get("text") or "")
        if text:
            yield StreamEvent(delta=text)
        yield StreamEvent(
            finish_reason="stop",
            output_tokens=body.get("output_tokens") or None,
        )


def _error(code: str, exc: Exception | None, *, transient: bool) -> StreamEvent:
    return StreamEvent(
        finish_reason="error",
        error_code=code,
        error_message=type(exc).__name__ if exc is not None else code,
        transient=transient,
    )


__all__ = ["HttpChatLLMGateway"]
