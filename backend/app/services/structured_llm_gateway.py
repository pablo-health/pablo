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

from .llm_telemetry import LLMSpanRequest, llm_span, usage_tokens

logger = logging.getLogger(__name__)


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
    ) -> StructuredCompletion:
        """Issue one structured completion and return the parsed JSON.

        ``response_schema`` is a JSON-schema-style dict (``{"type":
        "object", "properties": {...}}``). The Gemini implementation
        translates it to ``types.Schema`` internally.

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
        if self._client is None:
            from google import genai

            self._client = genai.Client(vertexai=True)
        return self._client

    @staticmethod
    def _normalize_model(model: str) -> str:
        """Strip a leading ``google:`` provider prefix (see chat gateway)."""
        if model.startswith("google:"):
            return model[len("google:") :]
        return model

    def complete_structured(
        self,
        *,
        model: str,
        system_prompt: str,
        user_prompt: str,
        response_schema: dict[str, Any],
        max_output_tokens: int,
        temperature: float = 0.3,
    ) -> StructuredCompletion:
        try:
            from google.genai import types
        except ImportError as exc:
            raise RuntimeError(
                "google-genai package is required for GeminiStructuredLLMGateway"
            ) from exc

        client = self._get_client()
        normalized_model = self._normalize_model(model)
        schema = _to_gemini_schema(types, response_schema)
        config = types.GenerateContentConfig(
            system_instruction=system_prompt,
            response_mime_type="application/json",
            response_schema=schema,
            temperature=temperature,
            max_output_tokens=max_output_tokens,
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

        raw_text = getattr(response, "text", "") or ""
        try:
            data = json.loads(raw_text)
        except json.JSONDecodeError as exc:
            raise ValueError(f"LLM returned invalid JSON: {exc}") from exc
        if not isinstance(data, dict):
            raise ValueError(f"LLM returned non-object JSON ({type(data).__name__})")

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
        return StructuredCompletion(
            data=data,
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
    ) -> StructuredCompletion:
        self.calls.append(
            {
                "model": model,
                "system_prompt": system_prompt,
                "user_prompt": user_prompt,
                "response_schema": response_schema,
                "max_output_tokens": max_output_tokens,
                "temperature": temperature,
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


__all__ = [
    "FakeStructuredLLMGateway",
    "GeminiStructuredLLMGateway",
    "StructuredCompletion",
    "StructuredLLMGateway",
    "get_default_structured_llm_gateway",
]
