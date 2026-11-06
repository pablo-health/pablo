# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

import pytest
from evals.anthropic_api import (
    DirectAnthropicGateway,
    _drop_null_properties,
    _strict_output_schema,
    direct_anthropic_gateway,
    direct_anthropic_model,
)


def test_direct_anthropic_model_removes_only_the_eval_prefix() -> None:
    assert direct_anthropic_model("anthropic-api:claude-sonnet-4-6") == "claude-sonnet-4-6"
    auto_model = direct_anthropic_model("anthropic-api-auto:claude-sonnet-4-6")
    assert auto_model == "auto:claude-sonnet-4-6"
    strict_model = direct_anthropic_model("anthropic-api-strict:claude-sonnet-5-5")
    assert strict_model == "strict:claude-sonnet-5-5"
    assert direct_anthropic_model("claude-sonnet-4-6") == "claude-sonnet-4-6"


def test_direct_anthropic_gateway_requires_its_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="ANTHROPIC_API_KEY"):
        direct_anthropic_gateway()


def test_direct_anthropic_gateway_injects_a_direct_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "eval-key")
    with patch("evals.anthropic_api.Anthropic") as client:
        gateway = direct_anthropic_gateway()
    client.assert_called_once_with(api_key="eval-key", timeout=180.0)
    assert gateway._client is client.return_value


_SCHEMA = {"type": "object", "properties": {"a": {"type": "string"}}, "required": ["a"]}


class _Messages:
    def __init__(self) -> None:
        self.sent: dict[str, Any] = {}

    def create(self, **kwargs: Any) -> SimpleNamespace:
        self.sent = kwargs
        return SimpleNamespace(
            content=[
                SimpleNamespace(type="tool_use", name="emit_structured_output", input={"a": "b"})
            ],
            stop_reason="tool_use",
            usage=SimpleNamespace(
                input_tokens=1,
                output_tokens=1,
                cache_read_input_tokens=None,
                cache_creation_input_tokens=None,
            ),
        )


def _sent_for(model: str) -> dict[str, Any]:
    messages = _Messages()
    gateway = DirectAnthropicGateway(client=SimpleNamespace(messages=messages))
    gateway.complete_structured(
        model=model,
        system_prompt="s",
        user_prompt="the text",
        response_schema=_SCHEMA,
        max_output_tokens=50,
        temperature=0.7,
    )
    return messages.sent


class TestWhatTheModelAccepts:
    """The eval gateway no longer names ``claude-sonnet-5-5``: ``claude_models`` decides."""

    @pytest.mark.parametrize("model", ["claude-sonnet-5-5", "claude-opus-5-5"])
    def test_a_model_that_rejects_a_forced_tool_is_asked_for_it(self, model: str) -> None:
        sent = _sent_for(model)

        assert sent["tool_choice"] == {"type": "auto"}
        assert "temperature" not in sent
        assert sent["messages"][0]["content"].endswith(
            "Call emit_structured_output with the result."
        )

    def test_haiku_5_5_is_forced_but_sent_no_temperature(self) -> None:
        sent = _sent_for("claude-haiku-5-5")

        assert sent["tool_choice"] == {"type": "tool", "name": "emit_structured_output"}
        assert "temperature" not in sent
        assert sent["messages"][0]["content"] == "the text"

    def test_an_earlier_model_is_unchanged(self) -> None:
        sent = _sent_for("claude-sonnet-4-6")

        assert sent["temperature"] == 0.7
        assert sent["tool_choice"] == {"type": "tool", "name": "emit_structured_output"}

    def test_the_auto_form_keeps_the_wording_results_were_recorded_with(self) -> None:
        """``auto:`` asks for the tool on any model, as it did when results were recorded."""
        sent = _sent_for("auto:claude-sonnet-4-6")

        assert sent["tool_choice"] == {"type": "auto"}
        assert "temperature" not in sent
        assert sent["tools"][0]["description"] == (
            "Always call this tool to return the requested structured result."
        )
        assert sent["messages"][0]["content"] == (
            "the text\n\nCall emit_structured_output with the result."
        )


def test_strict_output_schema_closes_objects_and_makes_optional_fields_nullable() -> None:
    schema = {
        "type": "object",
        "properties": {
            "required": {"type": "string"},
            "optional": {
                "type": "object",
                "properties": {"nested": {"type": "integer"}},
            },
        },
        "required": ["required"],
    }

    strict = _strict_output_schema(schema)

    assert strict["required"] == ["required", "optional"]
    assert strict["additionalProperties"] is False
    nested_object = strict["properties"]["optional"]
    assert nested_object["required"] == ["nested"]
    assert nested_object["additionalProperties"] is False
    assert nested_object["properties"]["nested"]["anyOf"][1] == {"type": "null"}
    assert schema["properties"]["optional"]["type"] == "object"


def test_strict_output_schema_uses_empty_members_inside_repeated_objects() -> None:
    schema = {
        "type": "object",
        "properties": {
            "items": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {"label": {"type": "string"}, "code": {"type": "string"}},
                    "required": ["label"],
                },
            }
        },
    }

    strict = _strict_output_schema(schema)

    item = strict["properties"]["items"]["anyOf"][0]["items"]
    assert item["required"] == ["label", "code"]
    assert item["properties"]["code"] == {"type": "string"}


def test_strict_output_schema_uses_empty_string_but_nullable_number() -> None:
    schema = {
        "type": "object",
        "properties": {"text": {"type": "string"}, "count": {"type": "integer"}},
    }

    strict = _strict_output_schema(schema)

    assert strict["properties"]["text"] == {"type": "string"}
    assert strict["properties"]["count"]["anyOf"][1] == {"type": "null"}


def test_drop_null_properties_restores_omitted_optional_fields() -> None:
    assert _drop_null_properties({"kept": [1, {"gone": None}], "gone": None}) == {"kept": [1, {}]}
