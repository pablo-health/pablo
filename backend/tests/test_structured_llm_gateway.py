# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Tests for the structured LLM gateway abstractions.

The Gemini impl is exercised end-to-end via the integration suite and
the note-generation tests (which inject a fake). Here we focus on the
contract that callers depend on: the fake replays in order, surfaces
exceptions, falls back to ``default_response``, and records calls.
"""

from __future__ import annotations

import pytest

from backend.app.services.structured_llm_gateway import (
    AnthropicStructuredLLMGateway,
    FakeStructuredLLMGateway,
    GeminiStructuredLLMGateway,
    StructuredCompletion,
    StructuredOutputTruncatedError,
    _to_gemini_schema,
    resolve_structured_llm_gateway,
)


class TestFakeStructuredLLMGateway:
    def test_returns_queued_responses_in_order(self) -> None:
        gw = FakeStructuredLLMGateway(
            responses=[
                StructuredCompletion(data={"a": 1}),
                StructuredCompletion(data={"b": 2}),
            ]
        )
        first = gw.complete_structured(
            model="m",
            system_prompt="s",
            user_prompt="u",
            response_schema={"type": "object"},
            max_output_tokens=64,
        )
        second = gw.complete_structured(
            model="m",
            system_prompt="s",
            user_prompt="u",
            response_schema={"type": "object"},
            max_output_tokens=64,
        )
        assert first.data == {"a": 1}
        assert second.data == {"b": 2}

    def test_falls_back_to_default_response(self) -> None:
        gw = FakeStructuredLLMGateway(
            default_response=StructuredCompletion(data={"fallback": True})
        )
        result = gw.complete_structured(
            model="m",
            system_prompt="s",
            user_prompt="u",
            response_schema={"type": "object"},
            max_output_tokens=64,
        )
        assert result.data == {"fallback": True}

    def test_raises_when_queue_empty_and_no_default(self) -> None:
        gw = FakeStructuredLLMGateway()
        with pytest.raises(RuntimeError, match="no queued response"):
            gw.complete_structured(
                model="m",
                system_prompt="s",
                user_prompt="u",
                response_schema={"type": "object"},
                max_output_tokens=64,
            )

    def test_queued_exception_is_raised(self) -> None:
        gw = FakeStructuredLLMGateway(responses=[ValueError("model said no")])
        with pytest.raises(ValueError, match="model said no"):
            gw.complete_structured(
                model="m",
                system_prompt="s",
                user_prompt="u",
                response_schema={"type": "object"},
                max_output_tokens=64,
            )

    def test_records_calls(self) -> None:
        gw = FakeStructuredLLMGateway(default_response=StructuredCompletion(data={}))
        gw.complete_structured(
            model="gemini-2.5-pro",
            system_prompt="sys",
            user_prompt="hello",
            response_schema={"type": "object", "properties": {"x": {"type": "string"}}},
            max_output_tokens=128,
            temperature=0.7,
        )
        assert len(gw.calls) == 1
        call = gw.calls[0]
        assert call["model"] == "gemini-2.5-pro"
        assert call["system_prompt"] == "sys"
        assert call["user_prompt"] == "hello"
        assert call["max_output_tokens"] == 128
        assert call["temperature"] == 0.7


class _StubType:
    OBJECT = "OBJECT"
    ARRAY = "ARRAY"
    STRING = "STRING"
    NUMBER = "NUMBER"
    INTEGER = "INTEGER"
    BOOLEAN = "BOOLEAN"


class _StubSchema:
    """Captures kwargs so we can assert on translated shape."""

    def __init__(self, **kwargs: object) -> None:
        self.kwargs = kwargs


class _StubTypes:
    Type = _StubType
    Schema = _StubSchema


class TestSchemaTranslation:
    """``_to_gemini_schema`` lifts JSON-schema dicts to ``types.Schema``.

    Verified with a stub ``types`` module so this test doesn't depend
    on ``google.genai`` being importable in the test env.
    """

    def test_nested_object_with_array_field(self) -> None:
        schema = {
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                "tags": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["title"],
        }
        result = _to_gemini_schema(_StubTypes(), schema)
        assert isinstance(result, _StubSchema)
        assert result.kwargs["type"] == "OBJECT"
        assert set(result.kwargs["properties"]) == {"title", "tags"}
        assert result.kwargs["required"] == ["title"]
        tags_schema = result.kwargs["properties"]["tags"]
        assert tags_schema.kwargs["type"] == "ARRAY"
        assert tags_schema.kwargs["items"].kwargs["type"] == "STRING"

    def test_scalar_types(self) -> None:
        for json_type, gemini_type in (
            ("string", "STRING"),
            ("number", "NUMBER"),
            ("integer", "INTEGER"),
            ("boolean", "BOOLEAN"),
        ):
            result = _to_gemini_schema(_StubTypes(), {"type": json_type})
            assert result.kwargs["type"] == gemini_type

    def test_nullable_and_enum_passthrough(self) -> None:
        schema = {
            "type": "string",
            "nullable": True,
            "enum": ["a", "b", "c"],
            "description": "letters",
        }
        result = _to_gemini_schema(_StubTypes(), schema)
        assert result.kwargs["nullable"] is True
        assert result.kwargs["enum"] == ["a", "b", "c"]
        assert result.kwargs["description"] == "letters"


# --- Anthropic (Claude on Vertex) structured gateway ---------------------------


class _FakeBlock:
    def __init__(self, *, type: str, name: str | None = None, input: object = None) -> None:
        self.type = type
        self.name = name
        self.input = input


class _FakeUsage:
    def __init__(self, input_tokens: int, output_tokens: int) -> None:
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens


class _FakeResponse:
    def __init__(self, *, content: list, stop_reason: str, usage: _FakeUsage) -> None:
        self.content = content
        self.stop_reason = stop_reason
        self.usage = usage


class _FakeMessages:
    def __init__(self, response: _FakeResponse, captured: dict) -> None:
        self._response = response
        self._captured = captured

    def create(self, **kwargs: object) -> _FakeResponse:
        self._captured.update(kwargs)
        return self._response


class _FakeAnthropic:
    def __init__(self, response: _FakeResponse) -> None:
        self.captured: dict = {}
        self.messages = _FakeMessages(response, self.captured)


def _tool_response(verdict: str = "block") -> _FakeResponse:
    return _FakeResponse(
        content=[
            _FakeBlock(
                type="tool_use",
                name="emit_structured_output",
                input={"verdict": verdict, "category": "medical_advice"},
            )
        ],
        stop_reason="tool_use",
        usage=_FakeUsage(120, 8),
    )


_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "verdict": {"type": "string", "enum": ["block", "allow"]},
        "category": {"type": "string"},
    },
    "required": ["verdict", "category"],
}


class TestAnthropicStructuredLLMGateway:
    def test_returns_forced_tool_input_as_data(self) -> None:
        client = _FakeAnthropic(_tool_response("block"))
        gw = AnthropicStructuredLLMGateway(client=client)
        result = gw.complete_structured(
            model="anthropic:claude-haiku-4-5",
            system_prompt="POLICY",
            user_prompt="candidate",
            response_schema=_SCHEMA,
            max_output_tokens=64,
        )
        assert result.data == {"verdict": "block", "category": "medical_advice"}
        assert result.output_tokens == 8
        assert result.finish_reason == "stop"

    def test_request_shape_strips_prefix_caches_system_and_forces_tool(self) -> None:
        client = _FakeAnthropic(_tool_response())
        gw = AnthropicStructuredLLMGateway(client=client)
        gw.complete_structured(
            model="anthropic:claude-haiku-4-5",
            system_prompt="POLICY",
            user_prompt="candidate",
            response_schema=_SCHEMA,
            max_output_tokens=64,
        )
        sent = client.captured
        # Provider prefix stripped for the Vertex publisher path.
        assert sent["model"] == "claude-haiku-4-5"
        # System prompt is sent as a cached block (token min on a repeated prefix).
        assert sent["system"][0]["text"] == "POLICY"
        assert sent["system"][0]["cache_control"] == {"type": "ephemeral"}
        # The caller's schema IS the forced tool's input schema.
        assert sent["tools"][0]["input_schema"] == _SCHEMA
        assert sent["tool_choice"] == {"type": "tool", "name": "emit_structured_output"}

    def test_truncation_raises(self) -> None:
        resp = _FakeResponse(content=[], stop_reason="max_tokens", usage=_FakeUsage(120, 64))
        gw = AnthropicStructuredLLMGateway(client=_FakeAnthropic(resp))
        with pytest.raises(StructuredOutputTruncatedError):
            gw.complete_structured(
                model="anthropic:claude-haiku-4-5",
                system_prompt="s",
                user_prompt="u",
                response_schema=_SCHEMA,
                max_output_tokens=64,
            )

    def test_missing_tool_call_raises(self) -> None:
        resp = _FakeResponse(
            content=[_FakeBlock(type="text", input=None)],
            stop_reason="end_turn",
            usage=_FakeUsage(120, 8),
        )
        gw = AnthropicStructuredLLMGateway(client=_FakeAnthropic(resp))
        with pytest.raises(ValueError, match="tool call"):
            gw.complete_structured(
                model="anthropic:claude-haiku-4-5",
                system_prompt="s",
                user_prompt="u",
                response_schema=_SCHEMA,
                max_output_tokens=64,
            )


class TestResolveStructuredLLMGateway:
    def test_anthropic_prefix_routes_to_claude(self) -> None:
        gw = resolve_structured_llm_gateway("anthropic:claude-haiku-4-5")
        assert isinstance(gw, AnthropicStructuredLLMGateway)

    def test_bare_and_google_route_to_gemini(self) -> None:
        assert isinstance(
            resolve_structured_llm_gateway("gemini-3.1-pro-preview"), GeminiStructuredLLMGateway
        )
        assert isinstance(
            resolve_structured_llm_gateway("google:gemini-3.1-pro"), GeminiStructuredLLMGateway
        )
