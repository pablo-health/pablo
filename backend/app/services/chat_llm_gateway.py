# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Streaming Gemini gateway for the chat primitive (THERAPY-5x5).

A thin wrapper around ``google.genai`` that exposes a streaming
completion call shaped for the chat turn service. The gateway is
intentionally narrow — it doesn't know about conversations, manifests,
or audit. It takes a model name, system prompt, and a list of
``UserAssistantTurn`` blocks and yields ``StreamEvent`` items.

Two implementations ship today:

- :class:`GeminiChatLLMGateway` — production. Initializes the
  ``google.genai`` client in Vertex AI mode, matching how
  ``embedding_service`` and ``ehr_navigation_service`` already do it.
  Per design doc §2.3 this is the HIPAA-Covered endpoint (GCP BAA),
  *not* the public AI Studio endpoint (no BAA).
- :class:`FakeChatLLMGateway` — used by tests. Deterministically
  replays a pre-recorded transcript so SSE/route tests don't need
  network.

Error semantics match design doc §8 + §14:

- ``safety_block`` → emitted once and the stream ends. No retry.
- ``error`` → emitted once and the stream ends. Retries happen one
  layer up in :class:`ChatTurnService`, not in the gateway itself.
- Normal completion → ``delta`` events follow by exactly one ``done``.
"""

from __future__ import annotations

import asyncio
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, ClassVar, Literal

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Iterable

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data shapes
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class UserAssistantTurn:
    """A single prior turn passed to the gateway as conversation context.

    ``role`` is ``"user"`` or ``"assistant"``; the gateway turns this
    into the model's native turn format. ``content`` is the displayed
    text — *not* the source-selection or manifest, which are part of
    the system prompt envelope, not the conversation stream.
    """

    role: Literal["user", "assistant"]
    content: str


FinishReason = Literal["stop", "length", "safety", "error"]


@dataclass(frozen=True)
class StreamEvent:
    """A single event in the streaming completion.

    Exactly one of ``delta``/``finish``/``error`` is set per event. The
    turn service translates these into SSE frames (``delta``/``done``/
    ``error``).
    """

    delta: str | None = None
    finish_reason: FinishReason | None = None
    output_tokens: int | None = None
    error_code: str | None = None
    error_message: str | None = None


# ---------------------------------------------------------------------------
# Gateway interface
# ---------------------------------------------------------------------------


class ChatLLMGateway(ABC):
    """Abstract streaming completion gateway used by the chat service."""

    # Declared without ``async`` so subclasses can ship ``async def`` generator
    # bodies — mypy treats ``async def`` + ``yield`` as ``AsyncIterator``, but
    # the abstract base must declare the iterator return type directly so the
    # subclass signature is compatible. See
    # https://mypy.readthedocs.io/en/stable/more_types.html#asynchronous-iterators.
    @abstractmethod
    def stream_completion(
        self,
        *,
        model: str,
        system_prompt: str,
        prior_turns: list[UserAssistantTurn],
        new_user_text: str,
        max_output_tokens: int,
        temperature: float = 0.4,
    ) -> AsyncIterator[StreamEvent]:
        """Stream a completion. Yields :class:`StreamEvent` items."""


# ---------------------------------------------------------------------------
# Gemini implementation
# ---------------------------------------------------------------------------


class GeminiChatLLMGateway(ChatLLMGateway):
    """Production gateway. Streams via ``google.genai`` on Vertex AI.

    See design doc §2.3 for the BAA posture: this client must be
    initialized in Vertex AI mode. AI-Studio mode (``api_key=...``) is
    *not* a Covered Service and must never be used for chat.
    """

    _SAFETY_FINISH_REASONS: ClassVar[frozenset[str]] = frozenset(
        {"SAFETY", "PROHIBITED_CONTENT", "RECITATION"}
    )
    _LENGTH_FINISH_REASONS: ClassVar[frozenset[str]] = frozenset({"MAX_TOKENS"})

    def __init__(self) -> None:
        self._client: Any = None

    def _get_client(self) -> Any:
        """Lazily initialize the Vertex AI client.

        Matches the lazy pattern used by ``embedding_service`` and
        ``ehr_navigation_service``: import inside the function so units
        that never call the gateway don't pay the import cost.
        """
        if self._client is None:
            from google import genai

            self._client = genai.Client(vertexai=True)
        return self._client

    async def stream_completion(  # noqa: PLR0915 — streaming pump + finish-reason handling
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
            from google.genai import types
        except ImportError as exc:
            raise RuntimeError("google-genai package is required for GeminiChatLLMGateway") from exc

        contents = _build_contents(types, prior_turns, new_user_text)
        config = types.GenerateContentConfig(
            system_instruction=system_prompt,
            temperature=temperature,
            max_output_tokens=max_output_tokens,
        )

        client = self._get_client()

        # The SDK exposes a synchronous streaming generator. We pump it
        # on a worker thread so the FastAPI event loop stays
        # responsive — the alternative (async streaming via the SDK's
        # async API) would tie us to a specific version.
        loop = asyncio.get_running_loop()
        queue: asyncio.Queue[StreamEvent | None] = asyncio.Queue()

        def _pump() -> None:
            try:
                stream = client.models.generate_content_stream(
                    model=model,
                    contents=contents,
                    config=config,
                )
                output_token_total = 0
                final_reason: FinishReason | None = None
                for chunk in stream:
                    delta_text = getattr(chunk, "text", None) or ""
                    if delta_text:
                        loop.call_soon_threadsafe(queue.put_nowait, StreamEvent(delta=delta_text))
                    candidates = getattr(chunk, "candidates", None) or []
                    for cand in candidates:
                        reason = getattr(cand, "finish_reason", None)
                        if reason is None:
                            continue
                        reason_str = str(reason).upper().rsplit(".", 1)[-1]
                        if reason_str in self._SAFETY_FINISH_REASONS:
                            final_reason = "safety"
                        elif reason_str in self._LENGTH_FINISH_REASONS:
                            final_reason = "length"
                        elif reason_str == "STOP":
                            final_reason = "stop"
                    usage = getattr(chunk, "usage_metadata", None)
                    if usage is not None:
                        ct = getattr(usage, "candidates_token_count", None)
                        if ct:
                            output_token_total = ct
                if final_reason is None:
                    final_reason = "stop"
                loop.call_soon_threadsafe(
                    queue.put_nowait,
                    StreamEvent(
                        finish_reason=final_reason,
                        output_tokens=output_token_total or None,
                    ),
                )
            except Exception as exc:
                logger.exception("Gemini stream failed")
                loop.call_soon_threadsafe(
                    queue.put_nowait,
                    StreamEvent(
                        finish_reason="error",
                        error_code=_classify_error(exc),
                        error_message=type(exc).__name__,
                    ),
                )
            finally:
                loop.call_soon_threadsafe(queue.put_nowait, None)

        future = loop.run_in_executor(None, _pump)
        try:
            while True:
                event = await queue.get()
                if event is None:
                    break
                yield event
        finally:
            # ``run_in_executor`` returns a Future we don't need to
            # await, but cancelling here prevents zombie threads if the
            # async iterator is closed early (e.g. client disconnect).
            future.cancel()


def _build_contents(
    types: Any,
    prior_turns: Iterable[UserAssistantTurn],
    new_user_text: str,
) -> list[Any]:
    """Render prior turns + the new user message as Gemini ``Content`` blocks.

    Role mapping: ``user`` → ``"user"``, ``assistant`` → ``"model"`` (the
    Gemini API's name for assistant output). Empty content rows are
    skipped — those would otherwise emit malformed blocks.
    """
    blocks: list[Any] = []
    for turn in prior_turns:
        content = (turn.content or "").strip()
        if not content:
            continue
        role = "user" if turn.role == "user" else "model"
        blocks.append(types.Content(role=role, parts=[types.Part(text=content)]))
    blocks.append(types.Content(role="user", parts=[types.Part(text=new_user_text)]))
    return blocks


def _classify_error(exc: Exception) -> str:
    """Return a stable, non-PHI error code for the SSE payload.

    Kept deliberately coarse — the design doc forbids stack traces or
    raw vendor messages in audit logs and stream payloads.
    """
    name = type(exc).__name__
    msg = str(exc).lower()
    if "timeout" in name.lower() or "timeout" in msg:
        return "timeout"
    if "unavailable" in msg or "503" in msg:
        return "service_unavailable"
    if "deadline" in msg:
        return "timeout"
    if "permission" in msg or "401" in msg or "403" in msg:
        return "auth_denied"
    return "llm_error"


# ---------------------------------------------------------------------------
# Test fake
# ---------------------------------------------------------------------------


@dataclass
class FakeChatLLMGateway(ChatLLMGateway):
    """Deterministic gateway for tests.

    Configure ``script`` with the events you want the next stream call
    to emit. Each call to ``stream_completion`` drains one script from
    ``scripts`` (or replays ``script`` if ``scripts`` is empty).
    """

    script: list[StreamEvent] = field(default_factory=list)
    scripts: list[list[StreamEvent]] = field(default_factory=list)
    calls: list[dict[str, Any]] = field(default_factory=list)

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
        self.calls.append(
            {
                "model": model,
                "system_prompt": system_prompt,
                "prior_turns": list(prior_turns),
                "new_user_text": new_user_text,
                "max_output_tokens": max_output_tokens,
                "temperature": temperature,
            }
        )
        events = self.scripts.pop(0) if self.scripts else list(self.script)
        for event in events:
            # Yield to the event loop between events so streaming
            # consumers can observe the partial state.
            await asyncio.sleep(0)
            yield event


__all__ = [
    "ChatLLMGateway",
    "FakeChatLLMGateway",
    "FinishReason",
    "GeminiChatLLMGateway",
    "StreamEvent",
    "UserAssistantTurn",
]
