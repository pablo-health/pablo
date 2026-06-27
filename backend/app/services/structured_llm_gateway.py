# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""One-shot structured-output Gemini gateway (THERAPY-71d5 / 9ijg).

Sibling to :mod:`chat_llm_gateway`. Where ``ChatLLMGateway`` streams
free-form chat deltas with prior turns, this gateway issues a single
non-streaming request that returns a JSON object validated against a
caller-supplied schema. Used by note generation and the eval scorers.

Two implementations ship:

- :class:`GeminiStructuredLLMGateway` — production. Initializes the
  ``google.genai`` client in Vertex AI mode (BAA-covered endpoint per
  design doc §2.3, *not* AI Studio). Uses Gemini's
  ``response_mime_type="application/json"`` + ``response_schema`` so
  the model returns a parsed JSON object directly.
- :class:`FakeStructuredLLMGateway` — used by tests. Deterministically
  replays a queued list of responses so note-gen tests don't need
  network or credentials.

Retries and quota live one layer up (in the calling service), matching
the pattern used by :mod:`chat_llm_gateway`.
"""

from __future__ import annotations

import json
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from .llm_provider import LLMProvider, strip_provider_prefix
from .llm_telemetry import LLMSpanRequest, llm_span, usage_tokens
from .vertex_client import anthropic_vertex_client, vertex_genai_client

logger = logging.getLogger(__name__)


class StructuredOutputTruncatedError(ValueError):
    """Raised when a structured completion is cut off at ``max_output_tokens``.

    Distinct from a generic invalid-JSON error so callers can retry with a
    larger output budget instead of treating it as a malformed response.
    Thinking models (e.g. ``gemini-3.x`` pro) spend part of the output
    budget on reasoning tokens before emitting the answer, so a cap that's
    fine for a non-thinking model can leave too little room for the JSON
    tail — the response comes back ``finish_reason=MAX_TOKENS`` with the
    JSON cut mid-string. See ``note_generation_service`` for the retry.
    """


@dataclass(frozen=True)
class StructuredCompletion:
    """Result of a single structured-output call.

    ``data`` is the parsed JSON object. ``output_tokens`` is the
    completion token count when the SDK reports one (used by usage
    metering); ``None`` if unavailable. ``finish_reason`` is one of
    ``"stop"``, ``"length"``, or ``"safety"`` — callers may want to
    treat anything other than ``"stop"`` as a soft failure.
    """

    data: dict[str, Any]
    output_tokens: int | None = None
    finish_reason: str = "stop"


class StructuredLLMGateway(ABC):
    """Abstract one-shot JSON-output gateway.

    Sync (unlike :class:`ChatLLMGateway` which is async-streaming) because
    note generation is a one-shot, sequential pipeline that runs from
    sync FastAPI service code; an async gateway here would force
    ``asyncio.run`` gymnastics or a sweeping sync→async refactor with
    no real benefit.
    """

    @abstractmethod
    def complete_structured(
        self,
        *,
        model: str,
        system_prompt: str,
        user_prompt: str,
        response_schema: dict[str, Any],
        max_output_tokens: int,
        temperature: float = 0.3,
        thinking_budget: int | None = None,
    ) -> StructuredCompletion:
        """Issue one structured completion and return the parsed JSON.

        ``response_schema`` is a JSON-schema-style dict (``{"type":
        "object", "properties": {...}}``). The Gemini implementation
        translates it to ``types.Schema`` internally.

        ``thinking_budget`` caps the model's reasoning tokens when set.
        Pass ``0`` to disable thinking entirely -- correct for verbatim /
        mechanical extraction (e.g. note import), where reasoning adds
        latency and cost without improving a copy-the-text task. Leave
        ``None`` to use the model's default thinking budget -- correct for
        genuine generation (e.g. SOAP from a transcript), where the
        reasoning is doing the work.

        Raises:
            ValueError: model returned invalid JSON or violated the schema.
            RuntimeError: transport / auth / SDK failure.
        """


class GeminiStructuredLLMGateway(StructuredLLMGateway):
    """Production gateway. One-shot generate_content via Vertex Gemini.

    Lifts the ``response_mime_type=application/json`` + ``response_schema``
    pattern from :mod:`ehr_navigation_service` so the SDK enforces the
    output shape rather than relying on prompt-only JSON discipline.
    """

    def __init__(self) -> None:
        self._client: Any = None

    def _get_client(self) -> Any:
        """Lazily build the Vertex client (shared factory)."""
        if self._client is None:
            self._client = vertex_genai_client()
        return self._client

    def complete_structured(
        self,
        *,
        model: str,
        system_prompt: str,
        user_prompt: str,
        response_schema: dict[str, Any],
        max_output_tokens: int,
        temperature: float = 0.3,
        thinking_budget: int | None = None,
    ) -> StructuredCompletion:
        # Never hold a pooled DB connection across the model round-trip — the
        # caller must release_db_connection() first (raises in dev/test).
        from ..db import assert_no_held_db_connection

        assert_no_held_db_connection("structured-llm")
        try:
            from google.genai import types
        except ImportError as exc:
            raise RuntimeError(
                "google-genai package is required for GeminiStructuredLLMGateway"
            ) from exc

        client = self._get_client()
        normalized_model = strip_provider_prefix(model)
        schema = _to_gemini_schema(types, response_schema)
        config = types.GenerateContentConfig(
            system_instruction=system_prompt,
            response_mime_type="application/json",
            response_schema=schema,
            temperature=temperature,
            max_output_tokens=max_output_tokens,
            # Default thinking when None; an explicit budget (incl. 0 to
            # disable) is forwarded for mechanical extraction callers.
            thinking_config=(
                types.ThinkingConfig(thinking_budget=thinking_budget)
                if thinking_budget is not None
                else None
            ),
        )

        with llm_span(LLMSpanRequest(operation="structured", model=normalized_model)) as span:
            try:
                response = client.models.generate_content(
                    model=normalized_model,
                    contents=user_prompt,
                    config=config,
                )
            except Exception as exc:
                logger.exception("Gemini structured completion failed")
                raise RuntimeError(f"Structured LLM call failed: {exc}") from exc
            prompt_tokens, output_tokens, total_tokens = usage_tokens(
                getattr(response, "usage_metadata", None)
            )
            span.set_token_usage(
                prompt_tokens=prompt_tokens,
                completion_tokens=output_tokens,
                total_tokens=total_tokens,
            )

        # Classify the finish reason *before* parsing: a length-truncated
        # response yields partial JSON, so we must distinguish "model hit the
        # token cap" from "model emitted malformed JSON" — otherwise the
        # JSONDecodeError below masks the real, retryable cause.
        finish_reason = "stop"
        candidates = getattr(response, "candidates", None) or []
        if candidates:
            raw_reason = getattr(candidates[0], "finish_reason", None)
            if raw_reason is not None:
                reason_str = str(raw_reason).upper().rsplit(".", 1)[-1]
                if reason_str in {"SAFETY", "PROHIBITED_CONTENT", "RECITATION"}:
                    finish_reason = "safety"
                elif reason_str == "MAX_TOKENS":
                    finish_reason = "length"

        raw_text = getattr(response, "text", "") or ""

        if finish_reason == "length":
            raise StructuredOutputTruncatedError(
                f"LLM output truncated at max_output_tokens={max_output_tokens} "
                f"(model={normalized_model}, {len(raw_text)} chars returned). "
                "Retry with a larger output budget."
            )

        try:
            data = json.loads(raw_text)
        except json.JSONDecodeError as exc:
            raise ValueError(f"LLM returned invalid JSON: {exc}") from exc
        if not isinstance(data, dict):
            raise ValueError(f"LLM returned non-object JSON ({type(data).__name__})")

        return StructuredCompletion(
            data=data,
            output_tokens=output_tokens,
            finish_reason=finish_reason,
        )


class AnthropicStructuredLLMGateway(StructuredLLMGateway):
    """Structured-output gateway backed by Claude on Vertex AI.

    Sibling to :class:`GeminiStructuredLLMGateway` for the Anthropic publisher
    models served through Vertex (the same BAA-covered endpoint, not the public
    Anthropic API). Anthropic has no ``response_schema`` knob, so the schema is
    enforced with a single forced tool call: the model must call
    ``emit_structured_output`` whose ``input_schema`` *is* the caller's
    ``response_schema``, and the validated tool input is the parsed JSON object.
    The static ``system_prompt`` is sent as a cached block, so a repeated prefix
    (the common case for a fixed instruction) bills at cache-read rates.

    Selected for ``anthropic:``-prefixed model ids by
    :func:`resolve_structured_llm_gateway`.
    """

    _TOOL_NAME = "emit_structured_output"

    def __init__(self, client: Any = None) -> None:
        # Injectable for tests; lazily built from the shared factory otherwise.
        self._client = client

    def _get_client(self) -> Any:
        if self._client is None:
            self._client = anthropic_vertex_client()
        return self._client

    def complete_structured(
        self,
        *,
        model: str,
        system_prompt: str,
        user_prompt: str,
        response_schema: dict[str, Any],
        max_output_tokens: int,
        temperature: float = 0.3,
        thinking_budget: int | None = None,
    ) -> StructuredCompletion:
        # Never hold a pooled DB connection across the model round-trip — the
        # caller must release_db_connection() first (raises in dev/test).
        from ..db import assert_no_held_db_connection

        assert_no_held_db_connection("structured-llm")

        # ``thinking_budget`` is part of the shared contract but unused here: the
        # Anthropic structured path issues a single non-thinking completion.
        del thinking_budget

        client = self._get_client()
        normalized_model = strip_provider_prefix(model)
        tool = {
            "name": self._TOOL_NAME,
            "description": "Return the result as structured output.",
            "input_schema": response_schema,
        }

        with llm_span(LLMSpanRequest(operation="structured", model=normalized_model)) as span:
            try:
                response = client.messages.create(
                    model=normalized_model,
                    max_tokens=max_output_tokens,
                    temperature=temperature,
                    system=[
                        {
                            "type": "text",
                            "text": system_prompt,
                            "cache_control": {"type": "ephemeral"},
                        }
                    ],
                    tools=[tool],
                    tool_choice={"type": "tool", "name": self._TOOL_NAME},
                    messages=[{"role": "user", "content": user_prompt}],
                )
            except Exception as exc:
                logger.exception("Anthropic structured completion failed")
                raise RuntimeError(f"Structured LLM call failed: {exc}") from exc

            usage = getattr(response, "usage", None)
            prompt_tokens = getattr(usage, "input_tokens", None)
            output_tokens = getattr(usage, "output_tokens", None)
            total = (
                (prompt_tokens or 0) + (output_tokens or 0)
                if prompt_tokens is not None or output_tokens is not None
                else None
            )
            span.set_token_usage(
                prompt_tokens=prompt_tokens,
                completion_tokens=output_tokens,
                total_tokens=total,
            )

        # Distinguish a token-capped completion (partial / no tool call) from a
        # genuine malformed response, mirroring the Gemini path's finish-reason
        # classification before parsing.
        stop_reason = getattr(response, "stop_reason", None)
        if stop_reason == "max_tokens":
            raise StructuredOutputTruncatedError(
                f"LLM output truncated at max_output_tokens={max_output_tokens} "
                f"(model={normalized_model}). Retry with a larger output budget."
            )
        finish_reason = "safety" if stop_reason == "refusal" else "stop"

        tool_input = next(
            (
                block.input
                for block in getattr(response, "content", None) or []
                if getattr(block, "type", None) == "tool_use"
                and getattr(block, "name", None) == self._TOOL_NAME
            ),
            None,
        )
        if tool_input is None:
            raise ValueError(
                f"Anthropic response had no '{self._TOOL_NAME}' tool call "
                f"(stop_reason={stop_reason})"
            )
        if not isinstance(tool_input, dict):
            raise ValueError(
                f"Structured tool input was not an object ({type(tool_input).__name__})"
            )

        return StructuredCompletion(
            data=tool_input,
            output_tokens=output_tokens,
            finish_reason=finish_reason,
        )


def _to_gemini_schema(types: Any, schema: dict[str, Any]) -> Any:
    """Translate a JSON-schema-style dict into a ``types.Schema``.

    Handles the subset Pablo's note registry actually produces: nested
    objects, arrays of strings, scalar string/number/boolean fields,
    and ``nullable``/``enum`` modifiers. Anything else is passed through
    as a typeless schema (Gemini will treat it permissively).
    """
    type_ = types.Type
    json_type = schema.get("type")
    kwargs: dict[str, Any] = {}

    if json_type == "object":
        kwargs["type"] = type_.OBJECT
        props = schema.get("properties") or {}
        kwargs["properties"] = {key: _to_gemini_schema(types, sub) for key, sub in props.items()}
        required = schema.get("required")
        if required:
            kwargs["required"] = list(required)
    elif json_type == "array":
        kwargs["type"] = type_.ARRAY
        items = schema.get("items") or {"type": "string"}
        kwargs["items"] = _to_gemini_schema(types, items)
    elif json_type == "string":
        kwargs["type"] = type_.STRING
    elif json_type == "number":
        kwargs["type"] = type_.NUMBER
    elif json_type == "integer":
        kwargs["type"] = type_.INTEGER
    elif json_type == "boolean":
        kwargs["type"] = type_.BOOLEAN

    if schema.get("nullable"):
        kwargs["nullable"] = True
    enum = schema.get("enum")
    if enum:
        kwargs["enum"] = list(enum)
    description = schema.get("description")
    if description:
        kwargs["description"] = description

    return types.Schema(**kwargs)


@dataclass
class FakeStructuredLLMGateway(StructuredLLMGateway):
    """Deterministic gateway for tests.

    Configure ``responses`` (a queue) or ``default_response`` (a fallback
    used when the queue is empty). Each ``complete_structured`` call pops
    one response off the queue. Recorded calls are exposed on ``calls``
    so tests can assert on prompts / schemas.
    """

    responses: list[StructuredCompletion | Exception] = field(default_factory=list)
    default_response: StructuredCompletion | None = None
    calls: list[dict[str, Any]] = field(default_factory=list)

    def complete_structured(
        self,
        *,
        model: str,
        system_prompt: str,
        user_prompt: str,
        response_schema: dict[str, Any],
        max_output_tokens: int,
        temperature: float = 0.3,
        thinking_budget: int | None = None,
    ) -> StructuredCompletion:
        self.calls.append(
            {
                "model": model,
                "system_prompt": system_prompt,
                "user_prompt": user_prompt,
                "response_schema": response_schema,
                "max_output_tokens": max_output_tokens,
                "temperature": temperature,
                "thinking_budget": thinking_budget,
            }
        )
        if self.responses:
            head = self.responses.pop(0)
            if isinstance(head, Exception):
                raise head
            return head
        if self.default_response is not None:
            return self.default_response
        raise RuntimeError(
            "FakeStructuredLLMGateway received a call with no queued response "
            "and no default_response configured"
        )


# Process-wide singleton. The gateway has no per-request state; the
# underlying ``google.genai`` client is lazily constructed inside.
# Wrapped in a list to keep the pattern consistent with
# :func:`chat_llm_gateway.get_chat_llm_gateway` (avoid module-level
# ``global``).
_default_gateway_holder: list[StructuredLLMGateway] = []


def get_default_structured_llm_gateway() -> StructuredLLMGateway:
    """Return the process-wide :class:`GeminiStructuredLLMGateway`.

    Lives next to the class (rather than in any single route module)
    because note generation is wired in from three different routes
    (sessions, notes, scheduling) plus the eval scorers. Tests override
    by injecting :class:`FakeStructuredLLMGateway` directly into
    :class:`RegistryNoteGenerationService`.
    """
    if not _default_gateway_holder:
        _default_gateway_holder.append(GeminiStructuredLLMGateway())
    return _default_gateway_holder[0]


_anthropic_gateway_holder: list[StructuredLLMGateway] = []


def _get_anthropic_structured_llm_gateway() -> StructuredLLMGateway:
    """Return the process-wide :class:`AnthropicStructuredLLMGateway`."""
    if not _anthropic_gateway_holder:
        _anthropic_gateway_holder.append(AnthropicStructuredLLMGateway())
    return _anthropic_gateway_holder[0]


def resolve_structured_llm_gateway(model: str) -> StructuredLLMGateway:
    """Pick the structured gateway for a (possibly provider-prefixed) model id.

    ``anthropic:claude-...`` routes to Claude on Vertex AI; a bare id or any
    other prefix (e.g. ``google:``) routes to the default Gemini gateway. Lets a
    single caller target either provider by model string alone, the same way
    :func:`strip_provider_prefix` already lets the prefix ride through config.
    """
    provider, sep, _rest = model.partition(":")
    if sep and provider == LLMProvider.ANTHROPIC:
        return _get_anthropic_structured_llm_gateway()
    return get_default_structured_llm_gateway()


__all__ = [
    "AnthropicStructuredLLMGateway",
    "FakeStructuredLLMGateway",
    "GeminiStructuredLLMGateway",
    "StructuredCompletion",
    "StructuredLLMGateway",
    "get_default_structured_llm_gateway",
    "resolve_structured_llm_gateway",
]
