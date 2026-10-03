# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Tests for the structured LLM gateway abstractions.

The Gemini impl is exercised end-to-end via the integration suite and
the note-generation tests (which inject a fake). Here we focus on the
contract that callers depend on: the fake replays in order, surfaces
exceptions, falls back to ``default_response``, and records calls.
"""

from __future__ import annotations

import json
import time
from typing import TYPE_CHECKING, Any

import httpx
import pytest

from backend.app.reliability import LLM_REQUEST
from backend.app.services.structured_llm_gateway import (
    AnthropicStructuredLLMGateway,
    FakeStructuredLLMGateway,
    GeminiStructuredLLMGateway,
    MistralStructuredLLMGateway,
    StructuredCompletion,
    StructuredOutputTruncatedError,
    _to_gemini_schema,
    resolve_structured_llm_gateway,
)

if TYPE_CHECKING:
    from collections.abc import Callable


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

    def __init__(self, **kwargs: Any) -> None:
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


class TestAnthropicAttemptTimeout:
    def test_timeout_reaches_the_request_only_when_set(self) -> None:
        client = _FakeAnthropic(_tool_response())
        gw = AnthropicStructuredLLMGateway(client=client)
        call = {
            "model": "anthropic:claude-haiku-4-5",
            "system_prompt": "s",
            "user_prompt": "u",
            "response_schema": _SCHEMA,
            "max_output_tokens": 64,
        }

        gw.complete_structured(**call)
        assert "timeout" not in client.captured

        gw.complete_structured(**call, timeout_seconds=10.0)
        assert client.captured["timeout"] == 10.0


class _GeminiResponse:
    text = '{"ok": true}'
    candidates = ()
    usage_metadata = None


class _StallingGeminiModels:
    """Raises the SDK's read timeout ``stalls`` times, then answers."""

    def __init__(self, stalls: int) -> None:
        self.stalls = stalls
        self.configs: list[Any] = []

    def generate_content(self, **kwargs: Any) -> _GeminiResponse:
        self.configs.append(kwargs["config"])
        if len(self.configs) <= self.stalls:
            raise httpx.ReadTimeout("The read operation timed out")
        return _GeminiResponse()


class TestGeminiAttemptTimeout:
    def _gateway(self, stalls: int) -> tuple[GeminiStructuredLLMGateway, _StallingGeminiModels]:
        models = _StallingGeminiModels(stalls)
        gw = GeminiStructuredLLMGateway()
        gw._client = type("_Client", (), {"models": models})()
        return gw, models

    def _complete(self, gw: GeminiStructuredLLMGateway, **extra: Any) -> StructuredCompletion:
        return gw.complete_structured(
            model="gemini-3.5-flash",
            system_prompt="s",
            user_prompt="u",
            response_schema={"type": "object", "properties": {"ok": {"type": "boolean"}}},
            max_output_tokens=64,
            **extra,
        )

    def test_timeout_bounds_each_attempt_in_milliseconds(self) -> None:
        gw, models = self._gateway(stalls=0)
        self._complete(gw, timeout_seconds=10.0)
        assert models.configs[0].http_options.timeout == 10_000

    def test_the_server_is_not_handed_the_short_deadline(self) -> None:
        """Vertex answers 504 well short of a deadline it is given, so the
        bound stays client-side and the server keeps the default one."""
        gw, models = self._gateway(stalls=0)
        self._complete(gw, timeout_seconds=10.0)
        assert models.configs[0].http_options.headers == {"X-Server-Timeout": "180"}

    def test_no_timeout_keeps_the_client_default(self) -> None:
        gw, models = self._gateway(stalls=0)
        self._complete(gw)
        assert models.configs[0].http_options is None

    def test_a_stalled_attempt_is_retried_once(self) -> None:
        gw, models = self._gateway(stalls=1)
        assert self._complete(gw, timeout_seconds=10.0).data == {"ok": True}
        assert len(models.configs) == 2

    def test_two_stalled_attempts_fail(self) -> None:
        gw, models = self._gateway(stalls=2)
        with pytest.raises(RuntimeError, match="Structured LLM call failed"):
            self._complete(gw, timeout_seconds=10.0)
        assert len(models.configs) == 2


assert LLM_REQUEST.deadline is not None
_DEADLINE: float = LLM_REQUEST.deadline


class _FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


class _ClockedGeminiModels:
    """Each failing attempt spends ``costs[i]`` seconds of fake time, capped
    at the attempt's own timeout, then raises the SDK's read timeout."""

    def __init__(self, clock: _FakeClock, costs: list[float]) -> None:
        self.clock = clock
        self.costs = costs
        self.timeouts_ms: list[int] = []

    def generate_content(self, **kwargs: Any) -> _GeminiResponse:
        timeout_ms = kwargs["config"].http_options.timeout
        self.timeouts_ms.append(timeout_ms)
        cost = self.costs[len(self.timeouts_ms) - 1]
        self.clock.now += min(cost, timeout_ms / 1000)
        raise httpx.ReadTimeout("The read operation timed out")


class TestGeminiCallDeadline:
    """A stalled first attempt and a retry fit inside LLM_REQUEST's deadline
    together, not 15 s apiece."""

    def _run(self, monkeypatch: pytest.MonkeyPatch, costs: list[float]) -> tuple[float, list[int]]:
        clock = _FakeClock()
        monkeypatch.setattr(time, "monotonic", clock)
        models = _ClockedGeminiModels(clock, costs)
        gw = GeminiStructuredLLMGateway()
        gw._client = type("_Client", (), {"models": models})()
        started = clock.now
        with pytest.raises(RuntimeError):
            gw.complete_structured(
                model="gemini-3.5-flash",
                system_prompt="s",
                user_prompt="u",
                response_schema={"type": "object"},
                max_output_tokens=64,
                timeout_seconds=15.0,
            )
        return clock.now - started, models.timeouts_ms

    def test_two_stalls_end_at_the_deadline(self, monkeypatch: pytest.MonkeyPatch) -> None:
        elapsed, timeouts_ms = self._run(monkeypatch, costs=[60.0, 60.0])

        assert timeouts_ms == [15_000, 10_000]
        assert elapsed <= _DEADLINE

    def test_a_fast_failure_leaves_the_retry_its_full_bound(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        elapsed, timeouts_ms = self._run(monkeypatch, costs=[2.0, 60.0])

        assert timeouts_ms == [15_000, 15_000]
        assert elapsed <= _DEADLINE


class TestMistralStructuredLLMGateway:
    def _ok_response(self, captured: dict, verdict: str = "block") -> Callable[[str, dict], dict]:
        def request(url: str, payload: dict) -> dict:
            captured["url"] = url
            captured["payload"] = payload
            return {
                "choices": [
                    {
                        "finish_reason": "tool_calls",
                        "message": {
                            "tool_calls": [
                                {
                                    "function": {
                                        "name": "emit_structured_output",
                                        "arguments": json.dumps(
                                            {"verdict": verdict, "category": "medical_advice"}
                                        ),
                                    }
                                }
                            ]
                        },
                    }
                ],
                "usage": {"prompt_tokens": 120, "completion_tokens": 8, "total_tokens": 128},
            }

        return request

    def test_rawpredict_request_shape_and_parse(self) -> None:
        captured: dict = {}
        gw = MistralStructuredLLMGateway(region="us-central1", request=self._ok_response(captured))
        result = gw.complete_structured(
            model="mistralai:mistral-small-2503",
            system_prompt="POLICY",
            user_prompt="candidate",
            response_schema={"type": "object", "properties": {"verdict": {"type": "string"}}},
            max_output_tokens=64,
        )
        assert result.data == {"verdict": "block", "category": "medical_advice"}
        assert result.output_tokens == 8
        # Provider prefix stripped; regional rawPredict endpoint; forced function.
        assert ":rawPredict" in captured["url"]
        assert "/publishers/mistralai/models/mistral-small-2503:rawPredict" in captured["url"]
        assert captured["payload"]["model"] == "mistral-small-2503"
        assert captured["payload"]["tool_choice"] == "any"
        assert captured["payload"]["tools"][0]["function"]["name"] == "emit_structured_output"

    def test_string_arguments_are_parsed(self) -> None:
        gw = MistralStructuredLLMGateway(
            region="us-central1", request=self._ok_response({}, "allow")
        )
        result = gw.complete_structured(
            model="mistralai:mistral-small-2503",
            system_prompt="s",
            user_prompt="u",
            response_schema={"type": "object"},
            max_output_tokens=64,
        )
        assert result.data["verdict"] == "allow"

    def test_missing_tool_call_raises(self) -> None:
        def request(url: str, payload: dict) -> dict:
            return {"choices": [{"finish_reason": "stop", "message": {"content": "no tool"}}]}

        gw = MistralStructuredLLMGateway(region="us-central1", request=request)
        with pytest.raises(ValueError, match="tool call"):
            gw.complete_structured(
                model="mistralai:mistral-small-2503",
                system_prompt="s",
                user_prompt="u",
                response_schema={"type": "object"},
                max_output_tokens=64,
            )

    def test_truncation_raises(self) -> None:
        def request(url: str, payload: dict) -> dict:
            return {"choices": [{"finish_reason": "length", "message": {}}]}

        gw = MistralStructuredLLMGateway(region="us-central1", request=request)
        with pytest.raises(StructuredOutputTruncatedError):
            gw.complete_structured(
                model="mistralai:mistral-small-2503",
                system_prompt="s",
                user_prompt="u",
                response_schema={"type": "object"},
                max_output_tokens=64,
            )


class TestResolveStructuredLLMGateway:
    def test_anthropic_prefix_routes_to_claude(self) -> None:
        gw = resolve_structured_llm_gateway("anthropic:claude-haiku-4-5")
        assert isinstance(gw, AnthropicStructuredLLMGateway)

    def test_mistral_prefix_routes_to_mistral(self) -> None:
        gw = resolve_structured_llm_gateway("mistralai:mistral-small-2503")
        assert isinstance(gw, MistralStructuredLLMGateway)

    def test_bare_and_google_route_to_gemini(self) -> None:
        assert isinstance(
            resolve_structured_llm_gateway("gemini-3.1-pro-preview"), GeminiStructuredLLMGateway
        )
        assert isinstance(
            resolve_structured_llm_gateway("google:gemini-3.1-pro"), GeminiStructuredLLMGateway
        )
